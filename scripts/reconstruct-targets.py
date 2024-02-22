"""
Generate synthetic trees for a set of specified query molecules. Multiprocessing.
"""  # TODO: Clean up + dont hardcode file paths
import json
import logging
import gzip
import multiprocessing as mp
from pathlib import Path
import networkx as nx
from typing import Tuple

import numpy as np
np.random.seed(42)
import pandas as pd
import pdb
from tqdm import tqdm
import os
import pickle
from copy import deepcopy

from synnet.config import DATA_PREPROCESS_DIR, DATA_RESULT_DIR, MAX_PROCESSES, MAX_DEPTH, NUM_POSS
from synnet.data_generation.preprocessing import BuildingBlockFileHandler, ReactionTemplateFileHandler
from synnet.visualize.drawers import MolDrawer, RxnDrawer
from synnet.visualize.writers import SynTreeWriter, SkeletonPrefixWriter
from synnet.visualize.visualizer import SkeletonVisualizer
from synnet.encoding.distances import cosine_distance
from synnet.models.common import find_best_model_ckpt, load_mlp_from_ckpt, load_gnn_from_ckpt
from synnet.models.gnn import PtrDataset
from synnet.models.mlp import nn_search_list
from synnet.MolEmbedder import MolEmbedder
from synnet.utils.data_utils import ReactionSet, SyntheticTreeSet, Skeleton, SkeletonSet, Program
from synnet.utils.predict_utils import mol_fp, synthetic_tree_decoder_greedy_search
import torch
from torch_geometric.data import Data

logger = logging.getLogger(__name__)


def _fetch_data_chembl(name: str) -> list[str]:
    raise NotImplementedError
    df = pd.read_csv(f"{DATA_DIR}/chembl_20k.csv")
    smis_query = df.smiles.to_list()
    return smis_query


def _fetch_data_from_file(name: str) -> list[str]:
    with open(name, "rt") as f:
        smis_query = [line.strip() for line in f]
    return smis_query


def _fetch_data(name: str) -> list[str]:
    if args.data in ["train", "valid", "test"]:
        file = (
            Path(DATA_PREPROCESS_DIR) / "syntrees" / f"synthetic-trees-filtered-{args.data}.json.gz"
        )
        logger.info(f"Reading data from {file}")
        syntree_collection = SyntheticTreeSet().load(file)
        smiles = [syntree.root.smiles for syntree in syntree_collection]
    elif args.data in ["chembl"]:
        smiles = _fetch_data_chembl(name)
    else:  # Hopefully got a filename instead
        smiles = _fetch_data_from_file(name)
    return smiles


def get_anc(cur, rxn_graph):
    lca = cur
    while rxn_graph.nodes[lca]['depth'] != MAX_DEPTH:
        ancs = list(rxn_graph.predecessors(lca))
        if ancs: 
            lca = ancs[0]
        else: 
            break         
    return lca         



def fill_in(args, sk, n, logits, bb_emb, rxn_templates, bbs, top_bb=1):
    """
    if rxn
        detect if n is within MAX_DEPTH of root
        if not fill in as usual
        if yes, find LCA (MAX_DEPTH from root), then use the subtree to constrain possibilities
    else
        find LCA (MAX_DEPTH from n), then use it to constrain possibilities
    """            
    rxn_graph, node_map, _ = sk.rxn_graph()       
    max_depth = max([rxn_graph.nodes[n]['depth'] for n in rxn_graph])
    if sk.rxns[n]:  
        cur = node_map[n]  
        mask_imposs = [False for _ in range(NUM_POSS)]            
        paths = []    
        if 'rxn' in args.filter_only:            
            attr_name = 'rxn_id_forcing' if args.forcing_eval else 'rxn_id'
            if max_depth < MAX_DEPTH or rxn_graph.nodes[cur]['depth'] == MAX_DEPTH:
                # try every reaction, and use existence of hash to filter possibilites            
                term = rxn_graph.nodes[cur][attr_name]
                for rxn_id in range(NUM_POSS):
                    rxn_graph.nodes[cur][attr_name] = rxn_id
                    p = Program(rxn_graph)
                    path = os.path.join(args.hash_dir, p.get_path())
                    mask_imposs[rxn_id] = not os.path.exists(path)
                    if os.path.exists(path):
                        paths.append(path)
                    else:
                        paths.append('')
                rxn_graph.nodes[cur][attr_name] = term
            elif rxn_graph.nodes[cur]['depth'] < MAX_DEPTH:
                lca = get_anc(cur, rxn_graph)
                # use prog_graph to hash, navigate the file system
                term = rxn_graph.nodes[cur][attr_name]
                for rxn_id in range(NUM_POSS):
                    rxn_graph.nodes[cur][attr_name] = rxn_id
                    p = Program(rxn_graph)
                    if 'path' not in rxn_graph.nodes[lca]:
                        breakpoint()
                    if rxn_graph.nodes[lca]['path'][-5:] == '.json':                
                        path_stem = rxn_graph.nodes[lca]['path'][:-5]                
                        path_stem = Path(path_stem).stem
                        path = os.path.join(args.hash_dir, path_stem, p.get_path())
                        mask_imposs[rxn_id] = not os.path.exists(path)
                        if os.path.exists(path):
                            paths.append(path)
                        else:
                            paths.append('')                        
                    else:
                        mask_imposs[rxn_id] = True
                        paths.append('')

                rxn_graph.nodes[cur][attr_name] = term            
            if sum(mask_imposs) < NUM_POSS:
                logits[n][-NUM_POSS:][mask_imposs] = float("-inf")        
        rxn_id = logits[n][-NUM_POSS:].argmax(axis=-1).item()     
        # Sanity check for forcing eval
        sk.modify_tree(n, rxn_id=rxn_id, suffix='_forcing' if args.forcing_eval else '')
        sk.tree.nodes[n]['smirks'] = rxn_templates[rxn_id]
        rxn_graph.nodes[cur]['rxn_id'] = rxn_id                   
        # mask the intermediates so they're not considered on frontier
        for succ in sk.tree.successors(n):
            if not sk.leaves[succ]:
                sk.mask = [succ]
        if 'rxn' in args.filter_only:
            sk.tree.nodes[n]['path'] = paths[rxn_id]
        # print("path", os.path.join(args.hash_dir, p.get_path()))            
    else:   
        assert sk.leaves[n]
        emb_bb = logits[n][:-NUM_POSS]
        pred = list(sk.tree.predecessors(n))[0]           
        if 'bb' in args.filter_only:            
            path = sk.tree.nodes[pred]['path']     
            exist = os.path.exists(path)
        else:
            exist = False
        if exist:                     
            e = str(node_map[pred])
            data = json.load(open(path))
            succs = list(sk.tree.successors(pred))
            second = len(succs) == 2 and n == succs[1]
            indices = [bbs.index(smi) for smi in data['bbs'][e][second]]
            bb_ind = nn_search_list(emb_bb, bb_emb[indices], top_k=top_bb).item()
            smiles = bbs[indices[bb_ind]]
        else:            
            bb_ind = nn_search_list(emb_bb, bb_emb, top_k=top_bb).item()                   
            smiles = bbs[bb_ind]
        sk.modify_tree(n, smiles=smiles, suffix='_forcing' if args.forcing_eval else '')    



def pick_node(sk, logits):
    """
    implement strategies here,
    each node returned will add a beam to decoding
    """
    # pick frontier-rxn with highest logit if there is frontier-rxn
    # else pick random bb
    best_conf = float("-inf")
    best_rxn_n = None
    for n in logits:
        if sk.rxns[n]:
            conf = logits[n][-NUM_POSS:].max()
            if conf > best_conf:
                best_conf = conf
                best_rxn_n = n
    if best_rxn_n is not None:
        return [n]
    else:
        return [n for n in logits if not sk.rxns[n]]


@torch.no_grad()
def wrapper_decoder(args, sk, model_rxn, model_bb, bb_emb, rxn_templates, bblocks, skviz=None):
    top_k = args.top_k
    """Generate a filled-in skeleton given the input which is only filled with the target."""
    model_rxn.eval()
    model_bb.eval()
    # Following how SynNet reports reconstruction accuracy, we decode top-3 reactants, 
    # corresponding to the first bb chosen
    # To make the code more general, we implement this with a stack
    sks = [sk]    
    final_sks = []
    while len(sks):
        sk = sks.pop(-1)
        if ((~sk.mask) & (sk.rxns | sk.leaves)).any():
            """
            while there's reaction nodes or leaf building blocks to fill in
                compute, for each vacant reaction node or vacant building block, the possible
                predict on all of these
                pick the highest confidence one
                fill it in
            """        
            
            # prediction problem        
            _, X, _ = sk.get_state(rxn_target_down_bb=True, rxn_target_down=True)
            for i in range(len(X)):
                if i != sk.tree_root and not sk.rxns[i] and not sk.leaves[i]:
                    X[i] = 0
            
            edges = sk.tree_edges
            tree_edges = np.concatenate((edges, edges[::-1]), axis=-1)
            edge_input = torch.tensor(tree_edges, dtype=torch.int64)        
            pe = PtrDataset.positionalencoding1d(32, len(X))
            x_input = np.concatenate((X, pe), axis=-1)        
            data_rxn = Data(edge_index=edge_input, x=torch.Tensor(x_input))
            data_bb = Data(edge_index=edge_input, x=torch.Tensor(x_input))
            logits_rxn = model_rxn(data_rxn)
            logits_bb = model_bb(data_bb)
            logits = {}
            frontier_nodes = [n for n in set(sk.frontier_nodes) if not sk.mask[n]]
            for n in frontier_nodes:
                if sk.rxns[n]:
                    logits[n] = logits_rxn[n]
                else:                
                    assert sk.leaves[n]               
                    logits[n] = logits_bb[n]        
            poss_n = pick_node(sk, logits)                        
            for n in poss_n:
                sk_n = deepcopy(sk)
                first_bb = sk_n.leaves[n] and (sk_n.leaves)[sk_n.mask == 1].sum() == 0
                if top_k > 1 and first_bb: # first bb                
                    for k in range(1, 1+top_k):
                        sk_copy = deepcopy(sk_n)
                        fill_in(args, sk_copy, n, logits, bb_emb, rxn_templates, bblocks, top_bb=k)
                        sks.append(sk_copy)
                else:
                    fill_in(args, sk_n, n, logits, bb_emb, rxn_templates, bblocks, top_bb=1)
                    sks.append(sk_n)
                    if skviz is not None:
                        mermaid_txt = skviz.write(node_mask=sk_n.mask)             
                        mask_str = ''.join(map(str,sk_n.mask))
                        outfile = skviz.path / f"skeleton_{sk_n.index}_{mask_str}.md"  
                        SynTreeWriter(prefixer=SkeletonPrefixWriter()).write(mermaid_txt).to_file(outfile)      
                        print(f"Generated markdown file.", os.path.join(os.getcwd(), outfile))            
        else:
            final_sks.append(sk)
    print(len(final_sks), "beams")
    return final_sks


def test_correct(sk, sk_true, rxns, method='preorder', forcing=False):
    if method == 'preorder':
        if forcing:
            for n in sk.tree:
                attrs = [attr for attr in list(sk.tree.nodes[n]) if '_forcing' in attr]
                for attr in attrs:
                    # we re-store the attributes containing predictions into 
                    # original attributes
                    sk.tree.nodes[n][attr[:-len('_forcing')]] = sk.tree.nodes[n][attr]
        total_incorrect = {}
        preorder = list(nx.dfs_preorder_nodes(sk.tree, source=sk.tree_root))
        correct = True
        seq_correct = []
        for i in preorder:                
            if sk.rxns[i]:
                if sk.tree.nodes[i]['rxn_id'] != sk_true.tree.nodes[i]['rxn_id']:
                    correct = False
                    total_incorrect[i] = 1                    
                seq_correct.append(i not in total_incorrect)
            elif sk.leaves[i]:
                if sk.tree.nodes[i]['smiles'] != sk_true.tree.nodes[i]['smiles']:
                    correct = False
                    total_incorrect[i] = 1   
                seq_correct.append(i not in total_incorrect)
        if forcing:                     
            return seq_correct
        else:
            return correct, total_incorrect
    else:
        # compute intermediates and target
        postorder = list(nx.dfs_postorder_nodes(sk.tree, source=sk.tree_root))
        for i in postorder:
            if sk.rxns[i]:
                reactants = tuple(sk.tree.nodes[j]['smiles'] for j in list(sk.tree.successors(i)))            
                if len(reactants) != rxns[sk.tree.nodes[i]['rxn_id']].num_reactant:
                    return False
                interm = rxns[sk.tree.nodes[i]['rxn_id']].run_reaction(reactants)              
                pred = list(sk.tree.predecessors(i))[0]
                if interm is None:
                    return False
                sk.tree.nodes[pred]['smiles'] = interm
        correct = sk.tree.nodes[sk.tree_root]['smiles'] == sk_true.tree.nodes[sk_true.tree_root]['smiles']
    return correct


def update(dic_total, dic):
    for k in dic:
        if k not in dic_total:
            dic_total[k] = 0
        dic_total[k] += dic[k]


def get_args():
    import argparse

    parser = argparse.ArgumentParser()
    # File I/O
    parser.add_argument(
        "--building-blocks-file",
        type=str,
        default="data/assets/building-blocks/enamine_us_matched.csv",  # TODO: change
        help="Input file with SMILES strings (First row `SMILES`, then one per line).",
    )
    parser.add_argument(
        "--rxn-templates-file",
        type=str,
        default="data/assets/reaction-templates/hb.txt",  # TODO: change
        help="Input file with reaction templates as SMARTS(No header, one per line).",
    )
    parser.add_argument(
        "--rxns_collection_file",
        type=str,
        default="data/assets/reaction-templates/reactions_hb.json.gz",
    )
    parser.add_argument(
        "--embeddings-knn-file",
        type=str,
        help="Input file for the pre-computed embeddings (*.npy).",
        default="data/assets/building-blocks/enamine_us_emb_fp_256.npy"
    )    
    parser.add_argument(
        "--ckpt-bb", type=str, help="Model checkpoint to use"
    )
    parser.add_argument(
        "--ckpt-rxn", type=str, help="Model checkpoint to use"
    )    
    parser.add_argument(
        "--syntree-set-file",
        type=str,
        help="Input file for the ground-truth syntrees to lookup target smiles in",
    )          
    parser.add_argument(
        "--skeleton-set-file",
        type=str,
        required=True,
        help="Input file for the ground-truth skeletons to lookup target smiles in",
    )                
    parser.add_argument(
        "--hash-dir",
        default="",
        required=True
    )
    parser.add_argument(
        "--out-dir"        
    )
    # Parameters
    parser.add_argument(
        "--data",
        type=str,
        default="test",
        help="Choose from ['train', 'valid', 'test', 'chembl'] or provide a file with one SMILES per line.",
    )
    parser.add_argument("--top-k", default=1, type=int)
    parser.add_argument("--filter-only", type=str, nargs='+', choices=['rxn', 'bb'], default=[])
    parser.add_argument("--forcing-eval", action='store_true')
    parser.add_argument("--test-correct-method", default='preorder', choices=['preorder', 'postorder'])
    # Visualization
    parser.add_argument("--mermaid", action='store_true')
    # Processing
    parser.add_argument("--ncpu", type=int, default=1, help="Number of cpus")
    parser.add_argument("--verbose", default=False, action="store_true")
    return parser.parse_args()


def main(args):
    logger.info("Start.")
    logger.info(f"Arguments: {json.dumps(vars(args),indent=2)}")

    # ... reaction templates
    rxns = ReactionSet().load(args.rxns_collection_file).rxns
    logger.info(f"Successfully read {args.rxns_collection_file}.")
    rxn_templates = ReactionTemplateFileHandler().load(args.rxn_templates_file)    

    # Load skeleton set
    sk_set = None
    if args.skeleton_set_file:        
        skeletons = pickle.load(open(args.skeleton_set_file, 'rb'))
        skeleton_set = SkeletonSet().load_skeletons(skeletons)        
        syntree_set_all = [st for v in skeletons.values() for st in v]
        syntree_set = []
        SKELETON_INDEX = []
        for ind in range(len(skeleton_set.sks)):
            # if len(list(skeleton_set.skeletons)[ind].reactions) == 2:            
            sk = skeleton_set.sks[ind]
            good = True
            for idx in range(len(sk.tree)):
                if sk.rxns[idx]:
                    rxn_id = sk.tree.nodes[idx]['rxn_id']
                    if rxns[rxn_id].num_reactant == 2:
                        # if one is bb, one is rxn, not covered by enumeration
                        succs = list(sk.tree.successors(idx))
                        if sk.leaves[succs[0]] ^ sk.leaves[succs[1]]:
                            good = False            
            if sk.rxns.sum() > 2:
                good = False
            if good:
                SKELETON_INDEX.append(ind)        
        TOP_BBS = ['CC(C)NS(=O)(=O)c1ccccc1C(=O)O', 'CCNS(=O)(=O)c1cc([N+](=O)[O-])ccc1C(=O)O', 'CC(C)(C)NC(=O)c1ccccc1C(=O)O', 'O=C(O)c1ccccc1C(=O)NC12CC3CC(CC(C3)C1)C2', 'O=C(O)c1ccccc1C(=O)Nc1ccc(-c2ccccc2)cc1', 'O=C(O)c1ccccc1C(=O)Nc1ccccc1', 'O=C(O)c1ccccc1C(=O)Nc1ccccn1', 'CC(C)S(=O)(=O)c1ccccc1C(=O)O', 'O=C(O)c1ccccc1S(=O)(=O)C(F)F', 'Nc1cc(Cl)ccc1S', 'Nc1c(F)cc(Br)cc1S', 'Nc1cccnc1S', 'Nc1c(Br)cc(Br)cc1C=O', 'Nc1c(Cl)cc(Cl)cc1C=O', 'Cc1ccc(Cl)c(C=O)c1N', 'Cc1cc(Cl)cc(C=O)c1N', 'O=C1C(=O)c2ccc(Br)cc2-c2ccccc21', 'Nc1ccc(Br)cc1C=O', 'Nc1cc(Cl)cc(Cl)c1C=O', 'Nc1c(C=O)ccc(Br)c1F', 'Nc1c(Cl)cccc1C=O', 'Nc1ccc(Br)c(F)c1C=O', 'Nc1c(F)ccc(Cl)c1C=O', 'Nc1c(O)c(Br)cc(Br)c1C(=O)O', 'O=C(C(=O)c1ccc(F)cc1)c1ccc(F)cc1', 'O=C1C(Br)C2C3CC4C(C(=O)C(Br)C42)C13', 'O=C1C2OC2C(=O)C2C1C1(Cl)C(Cl)=C(Cl)C2(Cl)C1(Cl)Cl', 'Nc1c(C(=O)O)ccc(Cl)c1O', 'COc1ccc(C(=O)C(=O)c2ccc(OC)cc2)cc1', 'Nc1c(O)ccc(Cl)c1C(=O)O', 'Br.Nc1c(C(=O)O)ccc(Cl)c1O', 'O=C1CCC(=O)C1Cl', 'N#CCC(N)=O', 'O=C(C(=O)c1ccc(-c2ccccc2)cc1)c1ccc(-c2ccccc2)cc1', 'O=C1C(=O)c2ccccc2-c2ccccc21', 'Nc1c(F)cccc1C=O', 'O=C(C(=O)c1ccccc1)c1ccccc1', 'COC(=O)C1CC(=O)C(C(=O)OC)CC1=O', 'O=C1C(Br)=CC2C1C1C=CC2(Br)C1=O', 'Cc1ccc(C(=O)C(=O)c2ccccc2)cc1C', 'Nc1c(O)cccc1C(=O)O', 'Nc1cc(Cl)c(Br)cc1C(=O)O', 'Cc1cccc(C=O)c1N', 'CCOC(=O)C1CC(=O)C(C(=O)OCC)CC1=O', 'O=C1CC(C(=O)CCl)C1', 'Nc1cc(F)c([N+](=O)[O-])cc1C(=O)O', 'CCOC(=O)C(CC(C)=O)C(=O)C1CC1', 'Nc1c(Br)ccc(Br)c1C(=O)O', 'Nc1cc(F)cc(F)c1C=O', 'CCOC(=O)C(CC(C)=O)C(C)=O', 'COc1cc(N)c(C(=O)O)c(Br)c1', 'Nc1cc(O)c(C(=O)O)cc1C(=O)O', 'CCOC(=O)C(CC(=O)C1CC1)C(C)=O', 'Nc1c(Br)ccc(F)c1C(=O)O', 'CCOC(=O)C(CC(=O)C1CC1)C(=O)C1CC1', 'COC1(OC)C2(Cl)C3C(=O)C4C5C(=O)C3C1(Cl)C5(Cl)C42Cl', 'Nc1c(I)cccc1C(=O)O', 'C=CC=CCN.Cl', 'Cc1c(Cl)cc(N)c(C(=O)O)c1Cl', 'CCOC(=O)C(C(C)=O)C(C)C(C)=O', 'Cc1cc(Cl)c(N)c(C(=O)O)c1', 'Cc1cc(Br)c(N)c(C(=O)O)c1', 'C=CC(C)=CCC(=O)O', 'COc1ccc(C(=O)O)c(N)c1Br', 'Nc1cc(Br)cc(C(=O)O)c1N', 'CCC(C)C1OC2(CCC1C)CC1CC(CC=C(C)C(OC3CC(OC)C(OC4CC(OC)C(O)C(C)O4)C(C)O3)C(C)C=CC=C3COC4C(O)C(C)=CC(C(=O)O1)C34O)O2', 'Cc1c(Br)ccc(N)c1C(=O)O.Cl', 'Nc1c(F)cc(Br)cc1C(=O)O', 'COc1cc(Cl)cc(N)c1C(=O)O', 'Cl.Nc1ccc(Br)c(F)c1C(=O)O', 'COc1cc(N)c(C(=O)O)cc1Cl', 'CC(C)C1=CC2=CCC3C(C)(C(=O)O)CCCC3(C)C2CC1', 'Nc1c(Br)cc(F)cc1C(=O)O', 'Nc1cccc(I)c1C(=O)O', 'CCC=CC=CC(C)O', 'O=C1CCC2C(=O)CCC12', 'CCOC(=O)C(CC(=O)C(C)(C)C)C(C)=O', 'Nc1cc(F)cc(Cl)c1C(=O)O', 'Nc1cc(Cl)c(I)cc1C(=O)O', 'Nc1ccc(Oc2ccc(Cl)cc2)cc1C(=O)O', 'C=CC=CCBr', 'Nc1c(F)ccc(Br)c1C(=O)O', 'CSc1ccc(N)cc1Br', 'Cc1c(N)c(C(=O)O)cc(F)c1Br', 'Nc1c(C(=O)O)cc(Cl)c(Br)c1Cl', 'O=C1CCC(=O)C12CC2', 'Nc1ccc(Br)cc1C(=O)O', 'Nc1c(Br)cc(Cl)cc1C(=O)O', 'Nc1ccc(Cl)cc1C(=O)O', 'C=CC=CCCN.Cl', 'Nc1ccc(I)cc1C(=O)O', 'COc1cc(OC)c(C(=O)O)c(N)c1Cl', 'Nc1c(C(=O)O)ccc(Cl)c1F', 'Br.Nc1c(Cl)cc(Br)cc1C(=O)O', 'Nc1cc(Cl)ccc1C(=O)O', 'CC1(C)C(=O)C2C(C1=O)C2(C)C', 'Cc1ccc(Br)c(N)c1C(=O)O', 'COc1ccccc1NS(=O)(=O)c1ccc(N)c(C(=O)O)c1', 'Nc1cc(Br)c(F)cc1C(=O)O', 'CCc1cc(Br)cc(C(=O)O)c1N']
        rep = set()
        for syntree in syntree_set_all:        
            index = skeleton_set.lookup[syntree.root.smiles][0].index    
            if len(skeleton_set.lookup[syntree.root.smiles]) == 1: # one skeleton per smiles                
                if index in SKELETON_INDEX:
                # if index in SKELETON_INDEX and index not in rep:
                    # rep.add(index)

                # if len(syntree.reactions) == 2:
                #     if rxns[syntree.reactions[0].rxn_id].num_reactant == 2:
                #         if rxns[syntree.reactions[1].rxn_id].num_reactant == 1:
                #             breakpoint()
                    syntree_set.append(syntree)
                # if index in [0,1,2,3,4]:
                #     syntree_set.append(syntree)        
        targets = [syntree.root.smiles for syntree in syntree_set]
        lookup = {}
        # Compute the gold skeleton
        all_smiles = dict(zip([st.root.smiles for st in syntree_set], range(len(syntree_set))))
        for i, target in enumerate(targets):
            sk = Skeleton(syntree_set[i], skeleton_set.lookup[target][0].index)
            if not np.array([sk.tree.nodes[n]['smiles'] in TOP_BBS for n in sk.tree if sk.leaves[n]]).all():
                continue                
            lookup[target] = sk
        targets = list(lookup)
        print(f"{len(targets)}/{len(syntree_set_all)} syntrees")
    else:

        # Load data ...
        logger.info("Start loading data...")
        # ... query molecules (i.e. molecules to decode)    
        targets = _fetch_data(args.data)
    

    # ... building blocks
    bblocks = BuildingBlockFileHandler().load(args.building_blocks_file)
    # A dict is used as lookup table for 2nd reactant during inference:
    bblocks_dict = {block: i for i, block in enumerate(bblocks)}
    logger.info(f"Successfully read {args.building_blocks_file}.")

    # # ... building block embedding
    bblocks_molembedder = (
        MolEmbedder().load_precomputed(args.embeddings_knn_file).init_balltree(cosine_distance)
    )
    bb_emb = bblocks_molembedder.get_embeddings()
    bb_emb = torch.as_tensor(bb_emb, dtype=torch.float32)
    logger.info(f"Successfully read {args.embeddings_knn_file} and initialized BallTree.")
    logger.info("...loading data completed.")
    # ... models
    logger.info("Start loading models from checkpoints...")  
    rxn_gnn = load_gnn_from_ckpt(Path(args.ckpt_rxn))
    bb_gnn = load_gnn_from_ckpt(Path(args.ckpt_bb))
    logger.info("...loading models completed.")

    # Decode queries, i.e. the target molecules.
    logger.info(f"Start to decode {len(targets)} target molecules.")

    if args.ncpu == 1:
        results = []
        total_incorrect = {} 
        if args.forcing_eval:
            correct_summary = {}
        else:
            total_correct = {}                   
    
        for no, smi in tqdm(enumerate(targets)):
            sk = deepcopy(lookup[smi])
            tree_id = str(np.array(sk.tree.edges))
            if tree_id not in total_incorrect: 
                total_incorrect[tree_id] = {}
            if not args.forcing_eval and tree_id not in total_correct:
                total_correct[tree_id] = {'correct': 0, 'total': 0}
            if args.forcing_eval and tree_id not in correct_summary:
                correct_summary[tree_id] = {'sum_pool_correct': 0, 'total_pool': 0}
            sk.clear_tree(forcing=args.forcing_eval)
            sk.modify_tree(sk.tree_root, smiles=smi)              
            if args.mermaid:
                skviz = SkeletonVisualizer(skeleton=sk, outfolder=args.out_dir).with_drawings(mol_drawer=MolDrawer, rxn_drawer=RxnDrawer)                       
            else:
                skviz = None  
            sks = wrapper_decoder(args, sk, rxn_gnn, bb_gnn, bb_emb, rxn_templates, bblocks, skviz)                                    
            best_correct_steps = []
            for sk in sks:
                if args.forcing_eval:
                    correct_steps = test_correct(sk, lookup[smi], rxns, method='preorder', forcing=True)
                    if sum(correct_steps) >= sum(best_correct_steps):
                        best_correct_steps = correct_steps                    
                else:
                    if args.test_correct_method == 'postorder':
                        correct = test_correct(sk, lookup[smi], rxns, method=args.test_correct_method)
                    else:
                        correct, incorrect = test_correct(sk, lookup[smi], rxns, method=args.test_correct_method)                        
                    if correct:
                        break
            if not correct:
                update(total_incorrect[tree_id], incorrect)
            if args.forcing_eval:
                # if not correct:
                #     # implement a procedure to check if target can be recovered another way
                #     breakpoint()
                correct_summary[tree_id]['sum_pool_correct'] += sum(best_correct_steps)
                correct_summary[tree_id]['total_pool'] += len(best_correct_steps)                
                correct_summary[tree_id]['step_by_step'] = correct_summary[tree_id]['sum_pool_correct']/correct_summary[tree_id]['total_pool']                
            else:
                total_correct[tree_id]['correct'] += correct
                total_correct[tree_id]['total'] += 1
            if args.forcing_eval:
                summary = {k: v['step_by_step'] for (k, v) in correct_summary.items()}
                print(f"step-by-step: {summary}")
            else:                
                # print(f"tree: {sk.tree.edges} total_incorrect: {total_incorrect}")
                summary = {k: v['correct']/v['total'] for (k, v) in total_correct.items()}
                print(f"total summary: {summary}, total incorrect: {total_incorrect}")            
        

    # else:
    #     for i in range(len(targets)):
    #         smi = targets[i]
    #         index = sk_set.lookup[smi].index            
    #         targets[i] = (targets[i], sk_coords)
    #     with mp.Pool(processes=args.ncpu) as pool:
    #         logger.info(f"Starting MP with ncpu={args.ncpu}")
    #         results = pool.starmap(wrapper_decoder, targets)
    logger.info("Finished decoding.")

    # Print some results from the prediction
    # Note: If a syntree cannot be decoded within `max_depth` steps (15),
    #       we will count it as unsuccessful. The similarity will be 0.
    decoded = [smi for smi, _, _ in results]
    similarities = [sim for _, sim, _ in results]
    trees = [tree for _, _, tree in results]

    recovery_rate = (np.asfarray(similarities) == 1.0).sum() / len(similarities)
    avg_similarity = np.mean(similarities)
    n_successful = sum([syntree is not None for syntree in trees])
    logger.info(f"For {args.data}:")
    logger.info(f"  Total number of attempted  reconstructions: {len(targets)}")
    logger.info(f"  Total number of successful reconstructions: {n_successful}")
    logger.info(f"  {recovery_rate=}")
    logger.info(f"  {avg_similarity=}")

    # Save to local dir
    # 1. Dataframe with targets, decoded, smilarities
    # 2. Synthetic trees of the decoded SMILES
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving results to {output_dir} ...")

    df = pd.DataFrame({"targets": targets, "decoded": decoded, "similarity": similarities})
    df.to_csv(f"{output_dir}/decoded_results.csv.gz", compression="gzip", index=False)
    df.to_csv(f"{output_dir}/decoded_results.csv", index=False)

    synthetic_tree_set = SyntheticTreeSet(sts=trees)
    synthetic_tree_set.save(f"{output_dir}/decoded_syntrees.json.gz")

    logger.info("Completed.")



if __name__ == "__main__":

    # Parse input args
    args = get_args()
    breakpoint()
    main(args)
