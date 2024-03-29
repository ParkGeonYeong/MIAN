import argparse
import yaml
import torch
import torch.backends.cudnn as cudnn
import torch.nn.functional as F
import torch.optim as optim
import os
import matplotlib.pyplot as plt
import random
from shutil import copyfile
from solver import Solver
from model.deeplab_res import DeeplabRes
from model.deeplab_digit import DeepDigits
from model.discriminator import DigitDiscriminator, OfficeDiscriminator
from model.classifier import Predictor
from dataset.multiloader import MultiDomainLoader
from utils.weight_init import weight_init
import json

def get_arguments():
    """Parse all the arguments provided from the CLI.

    Returns:
      A list of parsed arguments.
    """
    parser = argparse.ArgumentParser(description="DeepLab-ResNet Network")
    parser.add_argument("--gpu", type=int, nargs='+', default=None, required=True,
                        help="choose gpu device.")
    parser.add_argument("--yaml", type=str, default='config.yaml',
                        help="yaml pathway")
    parser.add_argument("--exp_name", type=str, default='', required=True,
                        help="")
    parser.add_argument("--exp_detail", type=str, default=None, required=False,
                        help="")

    # Test arguments
    parser.add_argument("--advcoeff", type=float, default=None, required=False,
                        help="")
    parser.add_argument("--no_MCD", default=True, required=False,
                        action='store_false', help="")
    parser.add_argument("--target", type=str, default=None, required=False,
                        help="")
    parser.add_argument("--task", type=str, default=None, required=False,
                        help="")
    parser.add_argument("--partial_domain", type=str, nargs='+', default=None,
                        help="")
    parser.add_argument("--optimizer", type=str, default=None, required=False,
                        help="")
    parser.add_argument("--SVD_ld", type=float, default=None, required=False,
                        help="")
    parser.add_argument("--SVD_k", type=int, default=None, required=False,
                        help="")
    parser.add_argument("--SVD_ld_adapt", default='exponential', required=False, help="auto, constant, exponential")
    parser.add_argument("--num_steps_stop", type=int, default=None, required=False,
                        help="")
    parser.add_argument("--batch_size", type=int, default=None, required=False,
                        help="")
    parser.add_argument("--resume", type=str, default=None, required=False, help="")

    return parser.parse_args()


def main(config, args, param_path):
    """Create the model and start the training."""

    # -------------------------------
    # Setting Horovod

    gpus_tobe_used = ','.join([str(gpuNum) for gpuNum in args.gpu])
    print('gpus_tobe_used: {}'.format(gpus_tobe_used))
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpus_tobe_used)

    # -------------------------------
    # Setting Test arguments
    if args.task is not None:
        t = args.task
        print('task: ', t)
        config['data']['task'] = t
    if args.partial_domain is not None:
        p = args.partial_domain
        print('partial_domain: ', p)
        config['data']['domain'][args.task] = p
        config['data']['num_domain'][args.task] = len(p)

    if args.advcoeff is not None:
        c = args.advcoeff
        print('advcoeff: ', c)
        config['train']['lambda']['base_model']['bloss_AdvFeat'][args.task] = c
    if args.no_MCD is not None:
        no_MCD = args.no_MCD
        print('MCD: ', no_MCD)
    if args.target is not None:
        t = args.target
        print('target: ', t)
        config['data']['target'] = t
    if args.SVD_ld is not None:
        s = args.SVD_ld
        print('SVD_en lambda: ', s)
        config['train']['SVD_ld'] = s
    if args.SVD_k is not None:
        k = args.SVD_k
        print('SVD_en k: ', k)
        config['train']['SVD_k'] = k
    if args.SVD_ld_adapt is not None:
        ab = args.SVD_ld_adapt
        print('SVD_ld adapt: ', ab)
        config['train']['SVD_ld_adapt'] = ab
    if args.num_steps_stop is not None:
        ns = args.num_steps_stop
        print('num_steps_stop: ', ns)
        config['train']['num_steps_stop'][args.task] = ns
    if args.batch_size is not None:
        bs = args.batch_size
        print('batch_size: ', bs)
        config['train']['batch_size'][args.task] = bs
    if args.optimizer is not None:
        o = args.optimizer
        print('optimizer: ', o)
        assert o == 'Momentum' or o == 'Adam'
        assert args.task is not None
        config['train']['optimizer'][args.task] = o

    with open(os.path.join(param_path, 'config.json'), 'w') as f:
        json.dump(config, f)

    # -------------------------------

    cudnn.enabled = True
    cudnn.benchmark = True
    gpu = args.gpu
    gpu_map = {
        'basemodel': 'cuda:0',
        'C': 'cuda:0',
        'netDFeat': 'cuda:0',
        'all_order': gpu
    }

    task = config['data']['task']
    num_classes = config['data']['num_classes'][task]
    input_size = config['data']['input_size'][task]
    cropped_size = config['data']['crop_size'][task]
    dataset = config['data']['domain'][task]
    dataset.remove(config['data']['target'])
    dataset = dataset + [config['data']['target']]
    print(dataset)
    num_workers = config['data']['num_workers']
    batch_size = config['train']['batch_size'][task]
    num_domain = len(dataset)

    base_lr = config['train']['base_model'][task]['lr']
    base_momentum = config['train']['base_model'][task]['momentum']
    D_lr = config['train']['netD'][task]['lr']
    D_momentum = config['train']['netD'][task]['momentum']
    weight_decay = config['train']['weight_decay']

    # ------------------------
    # 1. Create Model
    # ------------------------
    if task == 'digits':
        basemodel = DeepDigits(num_classes=num_classes)
        prev_feature_size = 2048
        basemodel.apply(weight_init)
    else:
        basemodel = DeeplabRes(num_classes=num_classes)
        prev_feature_size = 256

    basemodel.to(gpu_map['basemodel'])

    c1 = Predictor(prev_feature_size=prev_feature_size, num_classes=num_classes).to(gpu_map['C'])
    c2 = Predictor(prev_feature_size=prev_feature_size, num_classes=num_classes).to(gpu_map['C'])

    if task == 'digits':
        netDFeat = DigitDiscriminator(channel=prev_feature_size, num_domain=num_domain)
    else:
        netDFeat = OfficeDiscriminator(channel=prev_feature_size, num_domain=num_domain)
    netDFeat.to(gpu_map['netDFeat'])

    c1.apply(weight_init)
    c2.apply(weight_init)
    netDFeat.apply(weight_init)

    if args.resume is not None:
        checkpoint = torch.load(args.resume)
        basemodel.load_state_dict(checkpoint['basemodel'])
        print('load {}'.format(args.resume))

    # ------------------------
    # 2. Create DataLoader
    # ------------------------
    loader = MultiDomainLoader(dataset, '.', input_size, cropped_size, batch_size=batch_size,
                               shuffle=True, num_workers=num_workers, half_crop=None,
                               task=task)
    TargetLoader = loader.TargetLoader

    # ------------------------
    # 3. Create Optimizer and Solver
    # ------------------------
    DFeat_lr = D_lr

    if config['train']['optimizer'][task] == 'Momentum':
        print('Setting SGD Optimizer')
        optBase = optim.SGD(basemodel.parameters(), lr=base_lr, momentum=base_momentum, weight_decay=weight_decay)
        optC1 = optim.SGD(c1.parameters(), lr=10*base_lr, momentum=base_momentum, weight_decay=weight_decay)
        optC2 = optim.SGD(c2.parameters(), lr=10*base_lr, momentum=base_momentum, weight_decay=weight_decay)
        optDFeat = optim.SGD(netDFeat.parameters(), lr=DFeat_lr, momentum=D_momentum, weight_decay=weight_decay)
    elif config['train']['optimizer'][task] == 'Adam':
        print('Setting Adam Optimizer')
        optBase = optim.Adam(basemodel.parameters(), lr=base_lr, betas=(base_momentum, 0.99), weight_decay=weight_decay)
        optC1 = optim.Adam(c1.parameters(), lr=base_lr, betas=(base_momentum, 0.99), weight_decay=weight_decay)
        optC2 = optim.Adam(c2.parameters(), lr=base_lr, betas=(base_momentum, 0.99), weight_decay=weight_decay)
        optDFeat = optim.Adam(netDFeat.parameters(), lr=DFeat_lr, betas=(D_momentum, 0.99), weight_decay=weight_decay)


    solver = Solver(basemodel, c1, c2, netDFeat, loader, TargetLoader,
                    base_lr, DFeat_lr, task, num_domain, no_MCD,
                    optBase, optC1, optC2, optDFeat, config, args, gpu_map)
    # ------------------------
    # 4. Train
    # ------------------------

    solver.train()


if __name__ == '__main__':
    args = get_arguments()
    config = yaml.load(open(args.yaml, 'r'))

    snapshot_dir = config['exp_setting']['snapshot_dir']
    log_dir = config['exp_setting']['log_dir']
    exp_name = args.exp_name
    path_list = [os.path.join(snapshot_dir, exp_name), os.path.join(log_dir, exp_name)]

    for item in path_list:
        if not os.path.exists(item):
            os.makedirs(item)

    if args.exp_detail is not None:
        print(args.exp_detail)
        with open(os.path.join(log_dir, exp_name, 'exp_detail.txt'), 'w') as f:
            f.write(args.exp_detail+'\n')
            f.close()
    copyfile(args.yaml, os.path.join(log_dir, exp_name, 'config.yaml'))

    main(config, args, os.path.join(log_dir, exp_name))
