"""
Reaction network.
"""
import json
import logging
from pathlib import Path

import pytorch_lightning as pl
from pytorch_lightning import loggers as pl_loggers
from pytorch_lightning.callbacks.early_stopping import EarlyStopping
from pytorch_lightning.callbacks.model_checkpoint import ModelCheckpoint
from pytorch_lightning.callbacks.progress import TQDMProgressBar

from synnet.config import CHECKPOINTS_DIR
from synnet.models.common import get_args, xy_to_dataloader
from synnet.models.mlp import MLP

logger = logging.getLogger(__name__)
MODEL_ID = Path(__file__).stem

if __name__ == "__main__":
    logger.info("Start.")

    # Parse input args
    args = get_args()
    logger.info(f"Arguments: {json.dumps(vars(args),indent=2)}")

    pl.seed_everything(0)

    # Set up dataloaders
    dataset = "train"
    train_dataloader = xy_to_dataloader(
        X_file=Path(args.data_dir) / f"X_{MODEL_ID}_{dataset}.npz",
        y_file=Path(args.data_dir) / f"y_{MODEL_ID}_{dataset}.npz",
        n=None if not args.debug else 128,
        task="classification",
        batch_size=args.batch_size,
        num_workers=args.ncpu,
        shuffle=True if dataset == "train" else False,
    )

    dataset = "valid"
    valid_dataloader = xy_to_dataloader(
        X_file=Path(args.data_dir) / f"X_{MODEL_ID}_{dataset}.npz",
        y_file=Path(args.data_dir) / f"y_{MODEL_ID}_{dataset}.npz",
        n=None if not args.debug else 128,
        task="classification",
        batch_size=args.batch_size,
        num_workers=args.ncpu,
        shuffle=True if dataset == "train" else False,
    )
    logger.info(f"Set up dataloaders.")
    sk_dim = 0
    if args.skeleton_dir:        
        sk_dim = 256
    INPUT_DIMS = {
        "fp": {
            "hb": int(4 * args.nbits + sk_dim),
            "gin": int(4 * args.nbits + sk_dim),
        },
        "gin": {
            "hb": int(3 * args.nbits + args.out_dim),
            "gin": int(3 * args.nbits + args.out_dim),
        },
    }  # somewhat constant...
    input_dim = INPUT_DIMS[args.featurize][args.rxn_template]

    HIDDEN_DIMS = {
        "fp": {
            "hb": 3000,
            "gin": 4500,
        },
        "gin": {
            "hb": 3000,
            "gin": 3000,
        },
    }
    hidden_dim = HIDDEN_DIMS[args.featurize][args.rxn_template]

    OUTPUT_DIMS = {
        "hb": 91,
        "gin": 4700,
    }
    output_dim = OUTPUT_DIMS[args.rxn_template]

    ckpt_path = args.ckpt_file  # TODO: Unify for all networks
    mlp = MLP(
        input_dim=input_dim,
        output_dim=output_dim,
        hidden_dim=hidden_dim,
        num_layers=5,
        dropout=0.5,
        num_dropout_layers=1,
        task="classification",
        loss="cross_entropy",
        valid_loss="accuracy",
        optimizer="adam",
        learning_rate=3e-4,
        val_freq=1,
        ncpu=args.ncpu,
    )

    # Set up Trainer
    save_dir = Path("results/logs/") / MODEL_ID
    save_dir.mkdir(exist_ok=True, parents=True)

    tb_logger = pl_loggers.TensorBoardLogger(save_dir, name="")
    csv_logger = pl_loggers.CSVLogger(tb_logger.log_dir, name="", version="")
    logger.info(f"Log dir set to: {tb_logger.log_dir}")

    tb_logger = pl_loggers.TensorBoardLogger(save_dir, name="")

    checkpoint_callback = ModelCheckpoint(
        monitor="val_loss",
        dirpath=tb_logger.log_dir,
        filename="ckpts.{epoch}-{val_loss:.2f}",
        save_weights_only=False,
    )
    earlystop_callback = EarlyStopping(monitor="val_loss", patience=3)
    tqdm_callback = TQDMProgressBar(refresh_rate=int(len(train_dataloader) * 0.05))

    max_epochs = args.epoch if not args.debug else 100
    # Create trainer
    trainer = pl.Trainer(
        accelerator='gpu',
        devices=[1],
        max_epochs=max_epochs,
        callbacks=[checkpoint_callback, tqdm_callback],
        logger=[tb_logger, csv_logger],
        fast_dev_run=args.fast_dev_run,
        use_distributed_sampler=False
    )

    logger.info(f"Start training")
    trainer.fit(mlp, train_dataloader, valid_dataloader, ckpt_path=ckpt_path)
    logger.info(f"Training completed.")
