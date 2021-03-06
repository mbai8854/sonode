import time
import os
import argparse
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim


parser = argparse.ArgumentParser()
parser.add_argument('--tol', type=float, default=1e-3)
parser.add_argument('--adjoint', type=eval, default=False)
parser.add_argument('--niters', type=int, default=1000)
parser.add_argument('--lr', type=float, default=0.01)
parser.add_argument('--gpu', type=int, default=0)
parser.add_argument('--extra_dim', type=int, default=1)
parser.add_argument('--data_dimension', type=int, default=1)
parser.add_argument('--scale_factor', type=float, default=-1.0)
parser.add_argument('--ntrain', type=int, default=50)
parser.add_argument('--ntest', type=int, default=10)
parser.add_argument('--experiment_no', type=int, default=1)
args = parser.parse_args()

if args.adjoint:
    from torchdiffeq import odeint_adjoint as odeint
else:
    from torchdiffeq import odeint


class ODEfunc(nn.Module):

    def __init__(self, dim, nhidden):
        super(ODEfunc, self).__init__()
        self.elu = nn.ELU(inplace=True)
        self.fc1 = nn.Linear(dim, nhidden)
        self.fc2 = nn.Linear(nhidden, nhidden)
        self.fc3 = nn.Linear(nhidden, dim)
        self.nfe = 0

    def forward(self, t, x):
        self.nfe += 1
        out = self.fc1(x)
        out = self.elu(out)
        out = self.fc2(out)
        out = self.elu(out)
        out = self.fc3(out)
        return out


class ODEBlock(nn.Module):

    def __init__(self, odefunc, t0_, tN_, indices):
        super(ODEBlock, self).__init__()
        self.odefunc = odefunc
        self.integration_times = torch.tensor([t0_, tN_]).float()
        self.indices = indices
        
    def forward(self, x):
        out = odeint(self.odefunc, x, self.integration_times, rtol=args.tol, atol=args.tol)
        out = out[1].gather(1, self.indices)
        return out

    @property
    def nfe(self):
        return self.odefunc.nfe

    @nfe.setter
    def nfe(self, value):
        self.odefunc.nfe = value
       
        
def count_parameters(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)



if __name__ == '__main__':
    device = torch.device('cuda:' + str(args.gpu) if torch.cuda.is_available() else 'cpu')
    if args.extra_dim == 0:
        filename='node./'
    else:
        filename = 'anode./'
    
    try:
        os.makedirs('./'+filename)
    except FileExistsError:
        pass
    dim = args.data_dimension + args.extra_dim
    
    z0 = np.random.rand(args.ntrain, args.data_dimension)*2 - 1
    zN = z0*args.scale_factor
    z0 = torch.tensor(z0).float().to(device)
    zN = torch.tensor(zN).float().to(device)
    
    #Augment z0
    zeros = torch.zeros(args.ntrain, args.extra_dim).float()
    z0 = torch.cat((z0, zeros), dim=1).to(device)

    # model
    t0, tN = 0, 1
    nhidden = 20
    
    #make indices for unaugmenting
    ids = torch.arange(args.data_dimension)
    ids = ids.repeat(args.ntrain, 1)
    
    feature_layers = [ODEBlock(ODEfunc(dim, nhidden), t0, tN, ids)]
    model = nn.Sequential(*feature_layers).to(device)
    optimizer = optim.Adam(model.parameters(), lr=args.lr)
    loss_func = nn.MSELoss()

    itr_arr = np.empty(args.niters)
    loss_arr = np.empty(args.niters)
    nfe_arr = np.empty(args.niters)
    time_arr = np.empty(args.niters)
    
    # training
    start_time = time.time()
    for itr in range(1, args.niters + 1):
        feature_layers[0].nfe = 0
        iter_start_time= time.time()
        optimizer.zero_grad()

        # forward in time and solve ode
        pred_z = model(z0)
        # compute loss
        loss = loss_func(pred_z, zN)
        loss.backward()
        optimizer.step()
        iter_end_time = time.time()
        #make arrays
        itr_arr[itr-1] = itr
        loss_arr[itr-1] = loss
        nfe_arr[itr-1] = feature_layers[0].nfe
        time_arr[itr-1] = iter_end_time-iter_start_time
        
        print('Iter: {}, running MSE: {:.4f}'.format(itr, loss))
        
    
    end_time = time.time()
    results = np.load('results.npy')
    if args.extra_dim == 0:
        first_index = 0
    else:
        first_index = 1
    print('\n')
    print('Training complete after {} iters.'.format(itr))
    print('Time = ' + str(end_time-start_time))
    loss = loss_func(pred_z, zN).detach().numpy()
    results[first_index][0][int(args.data_dimension-1)][int(args.experiment_no-1)] = loss
    print('Train MSE = ' +str(loss))
    print('NFE = ' +str(feature_layers[0].nfe))
    print('Parameters = '+str(count_parameters(model)))
    
    np.save(filename+'itr_arr.npy', itr_arr)
    np.save(filename+'nfe_arr.npy', nfe_arr)
    np.save(filename+'loss_arr.npy', loss_arr)
    np.save(filename+'time_arr.npy', time_arr)
    torch.save(model, filename+'model.pth')


    #make test data
    z0 = np.random.rand(args.ntest, args.data_dimension)*2 - 1
    zN = z0*args.scale_factor
    z0 = torch.tensor(z0).float().to(device)
    zN = torch.tensor(zN).float().to(device)
    # augment z0
    zeros = torch.zeros(args.ntest, args.extra_dim).float()
    z0 = torch.cat((z0, zeros), dim=1).to(device)
    
    # indices to unaugment zN
    ids = torch.arange(args.data_dimension)
    ids = ids.repeat(args.ntest, 1)
    
    # run test data through network
    pred_z = odeint(model[0].odefunc, z0, torch.tensor([t0, tN]).float())
    pred_z = pred_z[1].gather(1, ids)

    # compute loss
    loss = loss_func(pred_z, zN).detach().numpy()
    results[first_index][1][int(args.data_dimension-1)][int(args.experiment_no-1)] = loss
    print('Test MSE = ' +str(loss))
    np.save('results.npy', results)
      