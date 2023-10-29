from synnet.utils.data_utils import SyntheticTree, SyntheticTreeSet
import pickle
import os
import networkx as nx
import matplotlib.pyplot as plt
import numpy as np
from tqdm import tqdm

def get_args():
    import argparse

    parser = argparse.ArgumentParser()
    # File I/O
    parser.add_argument(
        "--input-file",
        type=str,
        default="data/pre-process/syntrees/synthetic-trees.json.gz",
        help="Input file for the generated synthetic trees (*.json.gz)",
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
        help="Input file for the skeletons of syntree-file",
    )
    # Visualization args
    parser.add_argument(
        "--min_count",
        type=int,
        default=10
    )
    parser.add_argument(
        "--num_to_vis",
        type=int,
        default=10
    )   

    # Processing
    parser.add_argument("--ncpu", type=int, help="Number of cpus")
    return parser.parse_args()


def skeleton2graph(skeleton):
    graph = nx.MultiDiGraph()
    count = {}
    lookup = {}
    for n in skeleton.nodes:
        name = n.smiles
        if n.smiles in count:
            name += f":{count[n.smiles]}"
        graph.add_node(name)
        count[n.smiles] = count.get(n.smiles, 0)+1
        lookup[n] = name
    for e in skeleton.edges:
        graph.add_edge(lookup[skeleton.nodes[e[0]]], lookup[skeleton.nodes[e[1]]])
    return graph


def vis_skeletons(args, skeletons):
    min_count = args.min_count
    max_i = args.num_to_vis
    fig_path = os.path.join(args.visualize_dir, 'skeletons.png')
    fig = plt.Figure(figsize=(300, 300))
    for i in range(max_i):
        for j, sk in enumerate(skeletons):
            if len(skeletons[sk]) < min_count:
                continue
            ax = fig.add_subplot(max_i, len(skeletons), i*len(skeletons)+j+1)
            G = skeleton2graph(skeletons[sk][i])
            pos = nx.circular_layout(G)
            node_sizes = [200 for _ in G.nodes()]
            node_sizes[list(G.nodes()).index(skeletons[sk][i].root.smiles)] *= 2
            nx.draw_networkx(G, pos=pos, ax=ax, node_size=node_sizes)
    
    fig.savefig(fig_path)    
    print(f"visualized some skeletons at {fig_path}")


def count_skeletons(args, skeletons):
    fig_path = os.path.join(args.visualize_dir, 'skeletons_count.png')
    fig = plt.Figure()
    counts = [len(skeletons[k]) for k in skeletons]
    ax = fig.add_subplot(1, 1, 1)
    ax.bar(range(len(counts)), sorted(counts, key=lambda x:-x))
    ax.set_xlabel('skeleton')
    ax.set_ylabel('count')
    ax.set_yscale('log')
    fig.savefig(fig_path)
    print(f"visualized count at {fig_path}")


if __name__ == "__main__":
    args = get_args()
    syntree_collection = SyntheticTreeSet()
    syntrees = syntree_collection.load(args.input_file)

    if os.path.exists(args.skeleton_file):
        skeletons = pickle.load(open(args.skeleton_file, 'rb'))
    else:
        sts = []
        for st in syntree_collection.sts:
            if st: 
                try:
                    st.build_tree()
                except:
                    breakpoint()
                sts.append(st)
        
        skeletons = {}
        
        for i, st in tqdm(enumerate(sts)):
            print(i)
            done = False
            for sk in skeletons:
                if st.is_isomorphic(sk):
                    done = True
                    skeletons[sk].append(st)
                    break
            if not done: 
                skeletons[st] = [st]

                
        for k, v in skeletons.items():
            print(f"count: {len(v)}") 

        pickle.dump(skeletons, open(os.path.join(args.visualize_dir, 'skeletons.pkl'), 'wb+'))

    count_skeletons(args, skeletons)
    vis_skeletons(args, skeletons)