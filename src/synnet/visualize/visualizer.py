from pathlib import Path
from typing import Union

import sys
from synnet.utils.data_utils import NodeChemical, NodeRxn, SyntheticTree, SyntheticTreeSet
from synnet.visualize.drawers import MolDrawer
from synnet.visualize.writers import subgraph

from synnet.visualize.drawers import MolDrawer
from synnet.visualize.writers import SynTreeWriter

import pickle
import argparse


class SynTreeVisualizer:
    actions_taken: dict[int, str]
    CHEMICALS: dict[str, NodeChemical]
    outfolder: Union[str, Path]
    version: int

    ACTIONS = {
        0: "Add",
        1: "Expand",
        2: "Merge",
        3: "End",
    }

    def __init__(self, syntree: SyntheticTree, outfolder: str = "./syntree-viz/st"):
        self.syntree = syntree
        self.actions_taken = {
            depth: self.ACTIONS[action] for depth, action in enumerate(syntree.actions)
        }
        self.CHEMICALS = {node.smiles: node for node in syntree.chemicals}

        # Placeholder for images for molecues.
        self.drawer: Union[MolDrawer, None]
        self.molecule_filesnames: Union[None, dict[str, str]] = None

        # Folders
        outfolder = Path(outfolder)
        self.version = self._get_next_version(outfolder)
        self.path = outfolder.with_name(outfolder.name + f"_{self.version}")
        return None

    def _get_next_version(self, dir: str) -> int:
        root_dir = Path(dir).parent
        name = Path(dir).name

        existing_versions = []
        for d in Path(root_dir).glob(f"{name}_*"):
            d = str(d.resolve())
            existing_versions.append(int(d.split("_")[1]))

        if len(existing_versions) == 0:
            return 0

        return max(existing_versions) + 1

    def with_drawings(self, drawer: MolDrawer):
        """Init `MolDrawer` to plot molecules in the nodes."""
        self.path.mkdir(parents=True)
        self.drawer = drawer(self.path)
        return self

    def plot(self):
        """Plots molecules via `self.drawer.plot()`."""
        if self.drawer is None:
            raise ValueError("Must initialize drawer beforehand.")
        self.drawer.plot(self.CHEMICALS)
        self.molecule_filesnames = self.drawer.get_molecule_filesnames()
        return self

    def _define_chemicals(
        self,
        chemicals: dict[str, NodeChemical] = None,
    ) -> list[str]:
        chemicals = self.CHEMICALS if chemicals is None else chemicals

        if self.drawer.outfolder is None or self.molecule_filesnames is None:
            raise NotImplementedError("Must provide drawer via `_with_drawings()` before plotting.")

        out: list[str] = []

        for node in chemicals.values():
            name = f'"node.smiles"'
            name = f'<img src=""{self.drawer.outfolder.name}/{self.molecule_filesnames[node.smiles]}.svg"" height=75px/>'
            classdef = self._map_node_type_to_classdef(node)
            info = f"n{node.index}[{name}]:::{classdef}"
            out += [info]
        return out

    def _map_node_type_to_classdef(self, node: NodeChemical) -> str:
        """Map a node to pre-defined mermaid class for styling."""
        if node.is_leaf:
            classdef = "buildingblock"
        elif node.is_root:
            classdef = "final"
        else:
            classdef = "intermediate"
        return classdef

    def _write_reaction_connectivity(
        self, reactants: list[NodeChemical], product: NodeChemical
    ) -> list[str]:
        """Write the connectivity of the graph.
        Unimolecular reactions have one edge, bimolecular two.

        Examples:
            n1 --> n3
            n2 --> n3
        """
        NODE_PREFIX = "n"
        r1, r2 = reactants
        out = [f"{NODE_PREFIX}{r1.index} --> {NODE_PREFIX}{product.index}"]
        if r2 is not None:
            out += [f"{NODE_PREFIX}{r2.index} --> {NODE_PREFIX}{product.index}"]
        return out

    def write(self) -> list[str]:
        """Write markdown with mermaid block."""
        # 1. Plot images
        self.plot()
        # 2. Write markdown (with reference to image files.)
        rxns: list[NodeRxn] = self.syntree.reactions
        text = []

        # Add node definitions
        breakpoint()
        text.extend(self._define_chemicals(self.CHEMICALS))

        # Add paragraphs (<=> actions taken)
        for i, action in self.actions_taken.items():
            if action == "End":
                continue
            rxn = rxns[i]
            product: str = rxn.parent
            reactant1: str = rxn.child[0]
            reactant2: str = rxn.child[1] if rxn.rtype == 2 else None

            @subgraph(f'"{i:>2d} : {action}"')
            def __printer():
                return self._write_reaction_connectivity(
                    [self.CHEMICALS.get(reactant1), self.CHEMICALS.get(reactant2)],
                    self.CHEMICALS.get(product),
                )

            out = __printer()
            text.extend(out)
        return text


def demo():
    """Demo syntree visualisation"""
    # 1. Load syntree
    import json

    infile = "tests/assets/syntree-small.json"
    with open(infile, "rt") as f:
        data = json.load(f)

    st = SyntheticTree()
    st.read(data)

    outpath = Path("./figures/syntrees/generation/st")
    outpath.mkdir(parents=True, exist_ok=True)

    # 2. Plot & Write mermaid markup diagram
    stviz = SynTreeVisualizer(syntree=st, outfolder=outpath).with_drawings(drawer=MolDrawer)
    mermaid_txt = stviz.write()
    # 3. Write everything to a markdown doc
    outfile = stviz.path / "syntree.md"
    SynTreeWriter().write(mermaid_txt).to_file(outfile)
    print(f"Generated markdown file.")
    print(f"  Input file:", infile)
    print(f"  Output file:", outfile)
    return None


if __name__ == "__main__":
    # demo()
    parser = argparse.ArgumentParser()
    parser.add_argument('--syntree_pkl')
    parser.add_argument('--syntree_json')
    parser.add_argument('--out_folder')
    args = parser.parse_args()
    syntree_collection = SyntheticTreeSet().load(args.syntree_json)
    for st in syntree_collection:
        if st == None:
            breakpoint()
            continue
        breakpoint()
        stviz = SynTreeVisualizer(syntree=st, outfolder=args.out_folder).with_drawings(drawer=MolDrawer)
        mermaid_txt = stviz.write()        
        outfile = stviz.path / f"syntree.md"
        SynTreeWriter().write(mermaid_txt).to_file(outfile)
        print(f"Generated markdown file.", outfile)
        
    for st in syntree_collection:
        print(st.root.smiles)
