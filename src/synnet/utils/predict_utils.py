"""
This file contains various utils for creating molecular embeddings and for
decoding synthetic trees.
"""
from typing import Callable, Tuple

import numpy as np
import pytorch_lightning as pl
import rdkit
import torch
from rdkit import Chem
from sklearn.neighbors import BallTree

from synnet.encoding.distances import cosine_distance, tanimoto_similarity
from synnet.encoding.fingerprints import mol_fp
from synnet.encoding.utils import one_hot_encoder
from synnet.utils.data_utils import Reaction, SyntheticTree

# create a random seed for NumPy
np.random.seed(6)

# general functions
def can_react(state, rxns: list[Reaction]) -> Tuple[int, list[bool]]:
    """
    Determines if two molecules can react using any of the input reactions.

    Args:
        state (np.ndarray): The current state in the synthetic tree.
        rxns (list of Reaction objects): Contains available reaction templates.

    Returns:
        np.ndarray: The sum of the reaction mask tells us how many reactions are
             viable for the two molecules.
        np.ndarray: The reaction mask, which masks out reactions which are not
            viable for the two molecules.
    """
    mol1 = state.pop()
    mol2 = state.pop()
    reaction_mask = [int(rxn.run_reaction((mol1, mol2)) is not None) for rxn in rxns]
    return sum(reaction_mask), reaction_mask


def get_action_mask(state: list, rxns: list[Reaction]) -> np.ndarray:
    """
    Determines which actions can apply to a given state in the synthetic tree
    and returns a mask for which actions can apply.

    Args:
        state (np.ndarray): The current state in the synthetic tree.
        rxns (list of Reaction objects): Contains available reaction templates.

    Raises:
        ValueError: There is an issue with the input state.

    Returns:
        np.ndarray: The action mask. Masks out unviable actions from the current
            state using 0s, with 1s at the positions corresponding to viable
            actions.
    """
    # Action: (Add: 0, Expand: 1, Merge: 2, End: 3)
    if len(state) == 0:
        mask = [1, 0, 0, 0]
    elif len(state) == 1:
        mask = [1, 1, 0, 1]
    elif len(state) == 2:
        can_react_, _ = can_react(state, rxns)
        if can_react_:
            mask = [0, 1, 1, 0]
        else:
            mask = [0, 1, 0, 0]
    else:
        raise ValueError("Problem with state.")
    return np.asarray(mask, dtype=bool)


def get_reaction_mask(smi: str, rxns: list[Reaction]):
    """
    Determines which reaction templates can apply to the input molecule.

    Args:
        smi (str): The SMILES string corresponding to the molecule in question.
        rxns (list of Reaction objects): Contains available reaction templates.

    Raises:
        ValueError: There is an issue with the reactants in the reaction.

    Returns:
        reaction_mask (list of ints, or None): The reaction template mask. Masks
            out reaction templates which are not viable for the input molecule.
            If there are no viable reaction templates identified, is simply None.
        available_list (list of lists, or None): Contains available reactants if
            at least one viable reaction template is identified. Else is simply
            None.
    """
    # Return all available reaction templates
    # List of available building blocks if 2
    # Exclude the case of len(available_list) == 0
    reaction_mask = [int(rxn.is_reactant(smi)) for rxn in rxns]

    if sum(reaction_mask) == 0:
        return None, None

    available_list = []
    mol = rdkit.Chem.MolFromSmiles(smi)
    for i, rxn in enumerate(rxns):
        if reaction_mask[i] and rxn.num_reactant == 2:

            if rxn.is_reactant_first(mol):
                available_list.append(rxn.available_reactants[1])
            elif rxn.is_reactant_second(mol):
                available_list.append(rxn.available_reactants[0])
            else:
                raise ValueError("Check the reactants")

            if len(available_list[-1]) == 0:
                reaction_mask[i] = 0

        else:
            available_list.append([])

    return reaction_mask, available_list


def nn_search(
    _e: np.ndarray, _tree: BallTree, _k: int = 1
) -> Tuple[float, float]:  # TODO: merge w `nn_search_rt1`
    """
    Conducts a nearest neighbor search to find the molecule from the tree most
    simimilar to the input embedding.

    Args:
        _e (np.ndarray): A specific point in the dataset.
        _tree (sklearn.neighbors._kd_tree.KDTree, optional): A k-d tree.
        _k (int, optional): Indicates how many nearest neighbors to get.
            Defaults to 1.

    Returns:
        float: The distance to the nearest neighbor.
        int: The indices of the nearest neighbor.
    """
    dist, ind = _tree.query(_e, k=_k)
    return dist[0][0], ind[0][0]


def nn_search_rt1(_e: np.ndarray, _tree: BallTree, _k: int = 1) -> Tuple[np.ndarray, np.ndarray]:
    dist, ind = _tree.query(_e, k=_k)
    return dist[0], ind[0]


def set_embedding(
    z_target: np.ndarray, state: list[str], nbits: int, _mol_embedding: Callable
) -> np.ndarray:
    """
    Computes embeddings for all molecules in the input space.
    Embedding = [z_mol1, z_mol2, z_target]

    Args:
        z_target (np.ndarray): Molecular embedding of the target molecule.
        state (list): State of the synthetic tree, i.e. list of root molecules.
        nbits (int): Length of fingerprint.
        _mol_embedding (Callable): Computes the embeddings of molecules in the state.

    Returns:
        embedding (np.ndarray): shape (1,d+2*nbits)
    """
    z_target = np.atleast_2d(z_target)  # (1,d)
    if len(state) == 0:
        z_mol1 = np.zeros((1, nbits))
        z_mol2 = np.zeros((1, nbits))
    elif len(state) == 1:
        z_mol1 = np.atleast_2d(_mol_embedding(state[0]))
        z_mol2 = np.zeros((1, nbits))
    elif len(state) == 2:
        z_mol1 = np.atleast_2d(_mol_embedding(state[0]))
        z_mol2 = np.atleast_2d(_mol_embedding(state[1]))
    else:
        raise ValueError
    embedding = np.concatenate([z_mol1, z_mol2, z_target], axis=1)
    return embedding  # (1,d+2*nbits)


def synthetic_tree_decoder(
    z_target: np.ndarray,
    sk_coords: np.ndarray,
    building_blocks: list[str],
    bb_dict: dict[str, int],
    reaction_templates: list[Reaction],
    mol_embedder,
    action_net: pl.LightningModule,
    reactant1_net: pl.LightningModule,
    rxn_net: pl.LightningModule,
    reactant2_net: pl.LightningModule,
    bb_emb: np.ndarray,
    rxn_template: str,
    n_bits: int,
    max_step: int = 15,
    k_reactant1: int = 1,
) -> Tuple[SyntheticTree, int]:
    """
    Computes a synthetic tree given an input molecule embedding.
    Uses the Action, Reaction, Reactant1, and Reactant2 networks and a greedy search.

    Args:
        z_target (np.ndarray): Embedding for the target molecule
        building_blocks (list of str): Contains available building blocks
        bb_dict (dict): Building block dictionary
        reaction_templates (list of Reactions): Contains reaction templates
        mol_embedder (dgllife.model.gnn.gin.GIN): GNN to use for obtaining
            molecular embeddings
        action_net (synth_net.models.mlp.MLP): The action network
        reactant1_net (synth_net.models.mlp.MLP): The reactant1 network
        rxn_net (synth_net.models.mlp.MLP): The reaction network
        reactant2_net (synth_net.models.mlp.MLP): The reactant2 network
        bb_emb (list): Contains purchasable building block embeddings.
        rxn_template (str): Specifies the set of reaction templates to use.
        n_bits (int): Length of fingerprint.
        max_step (int, optional): Maximum number of steps to include in the
            synthetic tree

    Returns:
        tree (SyntheticTree): The final synthetic tree.
        act (int): The final action (to know if the tree was "properly"
            terminated).
    """
    # Initialization
    tree = SyntheticTree()
    mol_recent = None
    kdtree = mol_embedder  # TODO: dont mis-use this arg

    # Start iteration
    # TODO: tree decoder can exceed this an still return a tree, but action is not equal to 3
    # Raise error instead like in syntree generation?
    for i in range(max_step):
        # Encode current state
        state = tree.get_state()  # a list
        z_state = set_embedding(z_target, state, nbits=n_bits, _mol_embedding=mol_fp)
        if sk_coords is not None:
            z_state = np.concatenate((z_state, sk_coords), axis=1)

        # Predict action type, masked selection
        # Action: (Add: 0, Expand: 1, Merge: 2, End: 3)
        action_proba = action_net(torch.Tensor(z_state))  # (1,4)
        action_proba = action_proba.squeeze().detach().numpy() + 1e-10
        action_mask = get_action_mask(tree.get_state(), reaction_templates)
        act = np.argmax(action_proba * action_mask)

        # Continue growing tree?
        if act == 3:  # End
            break

        z_mol1 = reactant1_net(torch.Tensor(z_state))
        z_mol1 = z_mol1.detach().numpy()  # (1,dimension_output_embedding), default: (1,256)

        # Select first molecule
        if act == 0:
            # Select `k` for kNN search of 1st reactant
            # Use k>1 for the first action, and k==1 for all others.
            # Idea: Increase the chances of generating a better tree.
            k = k_reactant1 if mol_recent is None else 1

            _, idxs = kdtree.query(z_mol1, k=k)  # idxs.shape = (1,k)
            mol1 = building_blocks[idxs[0][k - 1]]
        elif act == 1 or act == 2:
            # Expand or Merge
            mol1 = mol_recent
        else:
            raise ValueError(f"Unexpected action {act}.")

        z_mol1 = mol_fp(mol1)
        z_mol1 = np.atleast_2d(z_mol1)  # (1,4096)

        # Select reaction
        z = np.concatenate([z_state, z_mol1], axis=1)
        reaction_proba = rxn_net(torch.Tensor(z))
        reaction_proba = reaction_proba.squeeze().detach().numpy() + 1e-10  # (nReactionTemplate,)

        if act == 0 or act == 1:  # add or expand
            reaction_mask, available_list = get_reaction_mask(mol1, reaction_templates)
        else:  # merge
            _, reaction_mask = can_react(tree.get_state(), reaction_templates)
            available_list = [
                [] for rxn in reaction_templates
            ]  # TODO: if act=merge, this is not used at all


        # If we ended up in a state where no reaction is possible, end this iteration.
        if reaction_mask is None:
            if len(state) == 1:  # only a single root mol, so this syntree is valid
                act = 3
                break
            else:
                break  # action != 3, so in our analysis we will see this tree as "invalid"

        # Select reaction template
        rxn_id = np.argmax(reaction_proba * reaction_mask)
        rxn = reaction_templates[rxn_id]

        NUMBER_OF_REACTION_TEMPLATES = {
            "hb": 91,
            "pis": 4700,
            "unittest": 3,
        }  # TODO: Refactor / use class

        # Select 2nd reactant
        if rxn.num_reactant == 2:
            if act == 2:  # Merge
                temp = set(state) - set([mol1])
                mol2 = temp.pop()
            else:  # Add or Expand
                x_rxn = one_hot_encoder(rxn_id, NUMBER_OF_REACTION_TEMPLATES[rxn_template])
                x_rct2 = np.concatenate([z_state, z_mol1, x_rxn], axis=1)
                z_mol2 = reactant2_net(torch.Tensor(x_rct2))
                z_mol2 = z_mol2.detach().numpy()
                available = available_list[rxn_id]  # list[str], list of reactants for this rxn
                available = [bb_dict[available[i]] for i in range(len(available))]  # list[int]
                temp_emb = bb_emb[available]
                available_tree = BallTree(
                    temp_emb, metric=cosine_distance
                )  # TODO: evaluate if distance matrix is faster/feasible as this BallTree is discarded immediately.
                dist, ind = nn_search(z_mol2, _tree=available_tree)
                mol2 = building_blocks[available[ind]]
        else:
            mol2 = None

        # Run reaction
        mol_product = rxn.run_reaction((mol1, mol2))
        if mol_product is None or Chem.MolFromSmiles(mol_product) is None:
            if len(state) == 1:  # only a single root mol, so this syntree is valid
                act = 3
                break
            else:
                break  # action != 3, so in our analysis we will see this tree as "invalid"

        # Update
        tree.update(act, int(rxn_id), mol1, mol2, mol_product)
        mol_recent = mol_product

    if act != 3:
        tree = tree
    else:
        tree.update(act, None, None, None, None)

    return tree, act


def synthetic_tree_decoder_greedy_search(
    beam_width: int = 3, analogs=False, **kwargs
) -> Tuple[str, float, SyntheticTree, int]:
    """
    Wrapper around `synthetic_tree_decoder_rt1` with variable `k` for kNN search of 1st reactant.
    Will keep the syntree that comprises of a molecule most similar to the target mol.

    Args:
        beam_width (int): The beam width to use for Reactant 1 search. Defaults to 3.
        kwargs: Identical to wrapped function.

    Returns:
        tree (SyntheticTree): The final synthetic tree
        act (int): The final action (to know if the tree was "properly" terminated)
    """
    z_target = kwargs["z_target"]
    trees: list[SyntheticTree] = []
    smiles: list[str] = []
    similarities: list[float] = []
    acts: list[int] = []

    for i in range(beam_width):
        tree, act = synthetic_tree_decoder(k_reactant1=i + 1, **kwargs)

        # Find the chemical in this tree that is most similar to the target.
        # Note: This does not have to be the final root mol, but any, as we can truncate tree to our liking.
        similarities_in_tree = np.array(
            tanimoto_similarity(z_target, [node.smiles for node in tree.chemicals])
        )
        try:
            max_similar_idx = np.argmax(similarities_in_tree)
        except ValueError:
            continue
        max_similarity = similarities_in_tree[max_similar_idx]

        # Keep track of max similarities (across syntrees)
        similarities.append(max_similarity)

        # Keep track of generated syntrees
        smiles.append(tree.chemicals[max_similar_idx].smiles)
        trees.append(tree)
        acts.append(act)

    if analogs:
        return smiles, similarities, trees, acts
    else:
        # Identify most similar among all trees
        max_similar_idx = np.argmax(similarities)
        similarity = similarities[max_similar_idx]
        tree = trees[max_similar_idx]
        smi = smiles[max_similar_idx]
        act = acts[max_similar_idx]

        return smi, similarity, tree, act
