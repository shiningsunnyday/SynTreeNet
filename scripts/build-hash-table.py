from synnet.config import MAX_PROCESSES
from synnet.data_generation.preprocessing import (
    BuildingBlockFileHandler,
    BuildingBlockFilter,
    ReactionTemplateFileHandler,
)
from synnet.utils.data_utils import SyntheticTree, SyntheticTreeSet, Skeleton, SkeletonSet, Program
from synnet.utils.analysis_utils import count_bbs, count_rxns
import pickle
import os
import networkx as nx
import matplotlib.pyplot as plt
from networkx.algorithms import dfs_tree, weisfeiler_lehman_graph_hash
from multiprocessing import Pool
from tqdm import tqdm
import numpy as np
from collections import defaultdict
from copy import deepcopy

def get_args():
    import argparse

    parser = argparse.ArgumentParser()
    # File I/O
    parser.add_argument(
        "--building-blocks-file",
        type=str,
        default="data/pre-process/building-blocks/enamine-us-smiles.csv.gz",  # TODO: change
        help="Input file with SMILES strings (First row `SMILES`, then one per line).",
    )
    parser.add_argument(
        "--rxn-templates-file",
        type=str,
        default="data/assets/reaction-templates/hb.txt",  # TODO: change
        help="Input file with reaction templates as SMARTS(No header, one per line).",
    )
    parser.add_argument(
        "--skeleton-file",
        type=str,
        default="results/viz/skeletons.pkl",
        help="Input file for the skeletons of syntree-file",
    )   
    parser.add_argument(
        "--visualize-dir",
        type=str,
        default="results/viz/",
        help="Where to visualize any figures",
    )        
    parser.add_argument(
        "--cache-dir",
        type=str,
        default="",
        help="Intermediate results",
    ) 
    # Processing
    parser.add_argument("--ncpu", type=int, default=1, help="Number of cpus")
    parser.add_argument("--top-bb", type=int)
    parser.add_argument("--top-rxn", type=int)
    parser.add_argument("--verbose", default=False, action="store_true")
    return parser.parse_args()


def get_wl_kernel(tree: nx.digraph, fill_in=[]):
    for n in tree.nodes():
        if n in fill_in:
            if 'rxn_id' in tree.nodes[n]:
                rxn_id = tree.nodes[n]['rxn_id']
                tree.nodes[n]['id'] = rxn_id
            elif 'smiles' in tree.nodes[n]:
                smiles = tree.nodes[n]['smiles']
                tree.nodes[n]['id'] = smiles   
            else:
                breakpoint()
        else:
            tree.nodes[n]['id'] = 0
    return weisfeiler_lehman_graph_hash(tree, iterations=len(tree), node_attr='id')



def vis_table(args, table_all):
    for length in table_all:
        table = table_all[length]
        fig_path = os.path.join(args.visualize_dir, f'hash_table_size={length}.png')
        fig = plt.Figure()
        counts = list(table.values())
        ax = fig.add_subplot(1, 1, 1)
        ax.bar(range(len(counts)), sorted(counts, key=lambda x:-x))
        ax.set_xlabel('filled-subtree hash')
        ax.set_ylabel('count')
        ax.set_yscale('log')
        ax.set_title(f'counts of length-{length} subtrees')
        fig.savefig(fig_path)
        print(f"visualized count at {fig_path}")    


def hash_st(st, index):
    sk = Skeleton(st, index)
    k_vals = []
    for node in sk.tree.nodes():
        if 'rxn_id' in sk.tree.nodes[node]:
            g = nx.dfs_tree(sk.tree, node)
            for n in g.nodes():
                for k in sk.tree.nodes[n]:
                    g.nodes[n][k] = sk.tree.nodes[n][k]

            bbs = [n for n in g.nodes() if list(g.successors(n)) == []]
            fill_in = [node] + [bbs]
            k_val = get_wl_kernel(g, fill_in=fill_in)
            k_vals.append((len(g), k_val))
    return k_vals


def get_programs(rxns, size=1):
    progs = []
    if size == 1:
        for i, rxn in enumerate(rxns):
            g = nx.DiGraph()
            g.add_node(0, rxn_id=i)
            # g.graph['super'] = False
            prog = Program(g)
            progs.append(prog)
        return {1: progs}
    all_progs = get_programs(rxns, size=size-1)
    for i, r in enumerate(rxns):
        if r.num_reactant == 1:
            A = all_progs[size-1]
            for a in A:
                prog = deepcopy(a)
                prog.add_rxn(i, len(a.rxn_tree)-1)
                progs.append(prog)
        else:
            for j in range(1, size-1):
                A = all_progs[j]
                B = all_progs[size-1-j]
                for a in A:
                    for b in B:                
                        prog = deepcopy(a).combine(deepcopy(b))
                        prog.add_rxn(i, 0, len(a.rxn_tree)-1)
                        progs.append(prog)
    all_progs[size] = progs
    return all_progs


def run_program(prog):
    prog.init_rxns(bbf.rxns)
    start_len, res = prog.run_rxn_tree()
    print(f"{len(res)}/{start_len} pass")
    return prog



def run_programs(progs, bbf, depths=[]):
    if depths == []:
        depths = progs.keys()
    
    for depth in depths:
        pass_rates = []
        print(f"running {len(progs[depth])} depth-{depth} programs")
        with Pool(bbf.processes) as p:
            pass_rates = p.map(run_program, tqdm(progs[depth]))

        #     pass_rates.append(len(res)/start_len)
        print(f"depth-{depth} pass rate: {np.mean(pass_rates)}")



def expand_program(i, a, b=None):
    prog = deepcopy(a)
    if b is not None:
        prog = prog.combine(deepcopy(b))      
        prog.add_rxn(i, len(a.rxn_tree)-1, len(prog.rxn_tree)-1)
    else:
        prog.add_rxn(i, len(a.rxn_tree)-1)
    return prog



def expand_programs(all_progs, size):
    progs = []
    pargs = []
    for i, r in enumerate(bbf.rxns):
        if r.num_reactant == 1:
            A = all_progs[size-1]
            for a in A:
                pargs.append((i, a))
        else:
            for j in range(1, size-1):
                A = all_progs[j]
                B = all_progs[size-1-j]
                for a in A:
                    for b in B:   
                        pargs.append((i, a, b))
    # with Pool(20) as p:
        # progs = p.starmap(expand_program,tqdm(pargs))
    progs = []
    for i, parg in enumerate(pargs):
        print(i)
        progs.append(expand_program(*parg))      
    all_progs[size] = progs
    return all_progs



def filter_programs(progs):
    new_progs = []
    for p in progs:
        good = True
        for e in p.entries:
            if np.prod([len(reactants) for reactants in p.rxn_map[e].available_reactants]):
                continue
            good = False
            break
        if good:
            new_progs.append(p)

    return new_progs



def create_run_programs(args, bbf, size=3):     
    if args.cache_dir:
        os.makedirs(args.cache_dir, exist_ok=True)
    for d in range(1, size+1):
        cache_fpath = os.path.join(args.cache_dir, f"{d}.pkl")
        exist = os.path.exists(cache_fpath)
        if args.cache_dir and exist:
            all_progs = pickle.load(open(cache_fpath, 'rb'))
            print(f"loaded {len(all_progs[d])} size-{d} programs")
            all_progs[d] = filter_programs(all_progs[d])
            assert d in all_progs
            continue
        if d == 1: 
            progs = get_programs(bbf.rxns, size=1)
            all_progs = progs
        else:  
            cache_fpath_pre = cache_fpath.replace(f"{d}.pkl", f"{d}_pre.pkl")
            if args.cache_dir and os.path.exists(cache_fpath_pre):
                all_progs = pickle.load(open(cache_fpath_pre, 'rb'))
            else:
                print(f"expanding {len(all_progs[d-1])} size-{d-1} programs")
                expand_programs(all_progs, d)
                if args.cache_dir:
                    pickle.dump(all_progs, open(cache_fpath_pre, 'wb'))
            print(f"create-running {len(all_progs[d])} size-{d} programs")
            # with Pool(bbf.processes) as p:
                # progs = p.map(run_program, tqdm(all_progs[d]))
  
        progs = [run_program(p) for p in tqdm(all_progs[d])]        
        all_progs[d] = filter_programs(progs)
        if args.cache_dir and not exist:
            pickle.dump(all_progs, open(cache_fpath, 'wb'))
        



if __name__ == "__main__":

    # Parse input args
    args = get_args()
    bblocks = BuildingBlockFileHandler().load(args.building_blocks_file)    
    rxn_templates = ReactionTemplateFileHandler().load(args.rxn_templates_file)

    if os.path.exists(args.skeleton_file):
        # Use to filter building blocks
        skeletons = pickle.load(open(args.skeleton_file, 'rb'))            
        if args.top_bb: 
            bb_counts = count_bbs(args, skeletons, vis=False)
            for bblock in bblocks:
                bb_counts[bblock]
            bblocks = sorted(bb_counts.keys(), key=lambda x:-bb_counts[x])                       
            bblocks = bblocks[:args.top_bb]
            print(f"top bb have counts: {[bb_counts[x] for x in bblocks]}")                
        if args.top_rxn:            
            rxn_counts = count_rxns(args, skeletons, vis=False)
            for rxn in rxn_templates:
                rxn_counts[rxn]            
            rxn_templates = sorted(rxn_templates, key=lambda x:-rxn_counts[x])        
            rxn_templates = rxn_templates[:args.top_rxn]
            print(f"top rxn have counts: {[rxn_counts[x] for x in rxn_templates]}")
        

    # debug
    test_st = list(skeletons.keys())[0]
    bblock_inds = [bblocks.index(n.smiles) for n in test_st.chemicals if n.smiles in bblocks]
    bblocks = [bblocks[ind] for ind in bblock_inds]
    rxn_templates = [rxn_templates[r.rxn_id] for r in test_st.reactions]


    bbf = BuildingBlockFilter(
        building_blocks=bblocks,
        rxn_templates=rxn_templates,
        verbose=args.verbose,
        processes=args.ncpu,
    )    

    # Count number of unique (uni-reaction, building block) pairs
    bbf._init_rxns_with_reactants()
    bbf.filter()

    # Run programs      
    # progs = get_programs(bbf.rxns, size=2)
    create_run_programs(args, bbf, size=3)

    skeletons = pickle.load(open(args.skeleton_file, 'rb'))
    sts = []
    for index, sk in enumerate(skeletons):
        for st in skeletons[sk]:
            sts.append([st, index])

    if args.ncpu == 1:
        res = [hash_st(st, index) for st, index in sts]
    else:
        with Pool(args.ncpu) as p:
            res = p.starmap(hash_st, tqdm(sts))
    res = [k_val for r in res for k_val in r]
    table = defaultdict(lambda: defaultdict(int))
    for length, k_val in res:
        table[length][k_val] += 1
    vis_table(args, table)
