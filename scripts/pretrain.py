from argparse import ArgumentParser

from torch import nn

import pytorch_lightning as pl
from pytorch_lightning.loggers import TensorBoardLogger
from pytorch_lightning.callbacks import LearningRateMonitor
from pl_bolts.datamodules import CIFAR10DataModule

import timm

from ssl_sandbox.pretrain import SimCLR, VICReg, VICRegOODDetection
from ssl_sandbox.eval import OnlineProbing
from ssl_sandbox.datamodules import CIFAR4vs6DataModule
from ssl_sandbox.pretrain.transforms import SimCLRViews

 
def parse_args():
    parser = ArgumentParser()
    
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--cifar10_dir', required=True)
    parser.add_argument('--log_dir', required=True)
    parser.add_argument('--method', required=True)

    parser.add_argument('--encoder', default='resnet50')
    parser.add_argument('--drop_rate', type=float, default=0.0)
    parser.add_argument('--drop_path_rate', type=float, default=0.0)
    parser.add_argument('--drop_block_rate', type=float, default=0.0)

    parser.add_argument('--batch_size', type=int, default=256)
    parser.add_argument('--num_workers', type=int, default=8)
    parser.add_argument('--lr', type=float, default=1e-2)
    parser.add_argument('--weight_decay', type=float, default=1e-6)
    parser.add_argument('--warmup_epochs', type=int, default=10)
    parser.add_argument('--num_epochs', type=int, default=1000)

    return parser.parse_args()


def adapt_to_cifar10(resnet: timm.models.ResNet):
    """See https://arxiv.org/pdf/2002.05709.pdf, Appendix B.9.
    """
    resnet.conv1 = nn.Conv2d(resnet.conv1.in_channels, resnet.conv1.out_channels,
                             kernel_size=3, padding=1, bias=False)
    resnet.maxpool = nn.Identity()
    return resnet


def main(args):
    if args.dataset == 'cifar10':
        dm = CIFAR10DataModule(
            data_dir=args.cifar10_dir,
            val_split=1000,
            num_workers=args.num_workers,
            normalize=True,
            batch_size=args.batch_size
        )
        image_size = 32
        blur = False
        jitter_strength = 0.5
    elif args.dataset == 'cifar4vs6':
        dm = CIFAR4vs6DataModule(
            data_dir=args.cifar10_dir,
            val_split=1000,
            num_workers=args.num_workers,
            normalize=True,
            batch_size=args.batch_size,
        )
        image_size = 32
        blur = False
        jitter_strength = 0.5
    else:
        raise ValueError(args.dataset)
    dm.train_transforms = SimCLRViews(
        size=image_size,
        jitter_strength=jitter_strength,
        blur=blur,
        final_transforms=dm.default_transforms()
    )
    dm.val_transforms = SimCLRViews(
        size=image_size,
        jitter_strength=jitter_strength,
        blur=blur,
        final_transforms=dm.default_transforms(),
        views_number=10
    )

    dropout_kwargs = dict(
        drop_rate=args.drop_rate,
        drop_path_rate=args.drop_path_rate,
        drop_block_rate=args.drop_block_rate
    )
    if args.encoder == 'resnet18':
        embed_dim = 512
        encoder = timm.models.resnet.resnet18(num_classes=embed_dim, **dropout_kwargs)
    elif args.encoder == 'resnet50':
        embed_dim = 2048
        encoder = timm.models.resnet.resnet50(num_classes=embed_dim, **dropout_kwargs)
    else:
        raise ValueError(args.encoder)
    if args.dataset in ['cifar10', 'cifar4vs6']:
        encoder = adapt_to_cifar10(encoder)

    optimizer_kwargs = dict(
        lr=args.lr,
        weight_decay=args.weight_decay,
        warmup_epochs=args.warmup_epochs
    )
    if args.method == 'simclr':
        model = SimCLR(encoder, embed_dim, **optimizer_kwargs)
    elif args.method == 'vicreg':
        model = VICReg(encoder, embed_dim, **optimizer_kwargs)
    else:
        raise ValueError(args.method)

    callbacks = [
        OnlineProbing(embed_dim, dm.num_classes),
        LearningRateMonitor()
    ]
    if args.method == 'vicreg':
        callbacks.append(VICRegOODDetection())

    trainer = pl.Trainer(
        logger=TensorBoardLogger(save_dir=args.log_dir, name=''),
        callbacks=callbacks,
        accelerator='gpu',
        max_epochs=args.num_epochs,
    )
    trainer.fit(model, datamodule=dm)


if __name__ == '__main__':
    main(parse_args())