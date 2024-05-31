from dataclasses import dataclass
from typing import Literal, Optional

import networkx as nx
import numpy as np
import pydantic


class GeneticSearchConfig(pydantic.BaseModel):
    """Configuration object for a genetic search.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    seed: int = 10

    # Individuals
    fp_bits: int = 2048
    bt_ignore: bool = False

    # Search parameters
    generations: int = 200
    population_size: int = 128
    offspring_size: int = 384

    analog_size: int = 128
    analog_delta: float = 0.01

    max_oracle_calls: int = int(1e7)

    # Mutation
    fp_mutate_prob: float = 0.5
    fp_mutate_frac: float = (24 / 4096)

    bt_mutate_prob: float = 0.5
    bt_mutate_edits: int = 3

    # Diversity vs. EI strategy
    explore_warmup: int = 15

    # Restrict skeleton prediction to max number of reactions
    max_num_rxns: int = -1

    # Early stopping
    early_stop: bool = False
    early_stop_delta: float = 0.01
    early_stop_warmup: int = 30
    early_stop_patience: int = 10

    # IO
    initialize_path: Optional[str] = None
    checkpoint_path: Optional[str] = None
    resume_path: Optional[str] = None
    background_set_file: str

    # WandB
    wandb: bool = False
    wandb_project: str = "syntreenet_ga_final"
    wandb_dir: Optional[str] = None


@dataclass
class Individual:
    """Individual object for a genetic search.
    """

    # Search space objects
    fp: np.ndarray
    bt: nx.DiGraph

    # Set by fitness function
    fitness: Optional[float] = None
    smiles: Optional[str] = None