import numbers
import numpy as np
import random

import torch
import torchvision


class RandomCrop(object):
    """Crops the given PIL.Image at a random location to have a region of
    the given size. size can be a tuple (target_height, target_width)
    or an integer, in which case the target will be of a square shape (size, size)
    """

    def __init__(self, size):
        if isinstance(size, numbers.Number):
            self.size = (int(size), int(size))
        else:
            self.size = size

    def __call__(self, tensors):
        output = []
        h, w = None, None
        th, tw = self.size
        for tensor in tensors:
            if h is None and w is None:
                _, h, w = tensor.size()
            elif tensor.size()[-2:] != (h, w):
                raise ValueError('Images must be same size')
        if w == tw and h == th:
            return tensors
        x1 = random.randint(0, w - tw)
        y1 = random.randint(0, h - th)
        for tensor in tensors:
            output.append(tensor[..., y1:y1 + th, x1:x1 + tw].contiguous())
        return output

class HalfCrop(object):
    """Crops halt the given PIL.Image randomly takes left or right to have a region of
    the given size. size can be a tuple (target_height, target_width)
    or an integer, in which case the target will be of a square shape (size, size)
    """

    def __call__(self, tensors):
        output = []
        th, tw = self.size
        tw_half = tw // 2
        left_side = random.randint(0, 1)
        x1 = 0 + left_side * tw_half  #random.randint(0, w - tw)
        for tensor in tensors:
            output.append(tensor[..., ..., x1:x1 + tw_half].contiguous())
        return output

class RandomHorizontalFlip(object):
    """Randomly horizontally flips the given PIL.Image with a probability of 0.5
    """

    def __call__(self, tensors):
        if random.random() < 0.5:
            output = []
            indices = torch.arange(tensors[0].size(-1) - 1, -1, -1).long()
            output.append(tensors[0].index_select(-1, indices))
            output.append(tensors[1])
            return output

        return tensors


def augment_collate(batch, crop=None, halfcrop=None, flip=True):
    transforms = []
    if crop is not None:
        transforms.append(RandomCrop(crop))
    if halfcrop is not None:
        transforms.append(HalfCrop())
    if flip:
        transforms.append(RandomHorizontalFlip())
    transform = torchvision.transforms.Compose(transforms)
    batch = [transform(x) for x in batch]
    return torch.utils.data.dataloader.default_collate(batch)

def to_tensor_raw(im):
    return torch.from_numpy(np.array(im, np.int32, copy=False))
