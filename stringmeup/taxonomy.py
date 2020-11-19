#!/usr/bin/env python3

import argparse
import logging
from collections import namedtuple
from dataclasses import dataclass, field
from os import path

logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d [%H:%M:%S]')
log = logging.getLogger(path.basename(__file__))

# TODO: make it possible to use scientific names in the same way as tax_id
@dataclass
class Node:
    name: str = None
    genbank_common_name: str = None
    rank: str = None
    parent: int = None
    children: list = field(default_factory=list)


Rank = namedtuple('Rank', ['rank_name', 'rank_code', 'rank_depth'])

# Using the same rank codes as Kraken 2 (https://github.com/DerrickWood/kraken2/blob/master/src/reports.cc)
translate_rank2code = {
    'superkingdom': 'D',
    'kingdom': 'K',
    'phylum': 'P',
    'class': 'C',
    'order': 'O',
    'family': 'F',
    'genus': 'G',
    'species': 'S'
}


class TaxonomyTreeException(Exception):
    pass


class TaxonomyTree:
    """
    Creates a representation of the taxonomy in the files names.dmp and
    nodes.dmp of a kraken2 database.

    Inspired by https://github.com/frallain/NCBI_taxonomy_tree.
    """

    def __init__(self, nodes_filename, names_filename):
        self.nodes_filename = nodes_filename
        self.names_filename = names_filename
        self.wanted_name_types = set(
            ['scientific name', 'genbank common name'])

        # Main data structure
        self.taxonomy = {}

        self.byranks = {}
        self.leaves = set()

        # "Memory" data structure to be populated at function calls
        # For faster response in case of same query is asked again
        self.lineages = {}
        self.distances = {}
        self.lca_mappings = {}

        # Add nodes to self.taxonomy
        self.construct_tree()

    def construct_tree(self):
        """
        Reads a names.dmp and nodes.dmp file, and constructs a taxonomy tree
        representation:
            {tax_id#1: Node('name', 'genbank_common_name', 'rank', 'parent', 'children'),
             tax_id#2: Node('name', 'genbank_common_name', 'rank', 'parent', 'children'),
             ...,
             tax_id#N: ...}
        """

        log.info("Constructing taxonomy tree...")
        taxid2name = {}

        try:
            log.info('Mapping taxonomic ID to scientific and genbank common names from "{names_file}"...'.format(names_file=self.names_filename))
            # TODO: check so that names.dmp conforms to expected format
            with open(self.names_filename, 'r') as f:
                for name_line in f:
                    name_info = name_line.split('|')
                    name_type = name_info[3].strip()
                    if name_type not in self.wanted_name_types:
                        continue

                    tax_id = int(name_info[0].strip())
                    if tax_id not in taxid2name:
                        taxid2name[tax_id] = {
                            'scientific_name': None,
                            'genbank_common_name': None}

                    tax_name = name_info[1].strip()

                    if name_type == 'scientific name':
                        if taxid2name[tax_id]['scientific_name'] is not None:
                            # Some logical checking, should only be one scientific name for a tax_id
                            raise TaxonomyTreeException("Found more than one scientific name for a unique tax_id. The tax_id was '{}'".format(tax_id))
                        taxid2name[tax_id]['scientific_name'] = tax_name

                    elif name_type == 'genbank common name':
                        if taxid2name[tax_id]['genbank_common_name'] is not None:
                            # Some logical checking, should only be one genbank common name for a tax_id
                            raise TaxonomyTreeException("Found more than one genbank common name for a unique tax_id. The tax_id was '{}'".format(tax_id))
                        taxid2name[tax_id]['genbank_common_name'] = tax_name

                    else:
                        raise TaxonomyTreeException("Logical error. Should not end up here. name_type was '{}'".format(tax_name))

        except FileNotFoundError:
            log.exception('Could not find the file "{names_file}".'.format(names_file=self.names_filename))
            raise

        try:
            log.info('Reading taxonomy from "{nodes_file}"...'.format(nodes_file=self.nodes_filename))
            # TODO: check so that nodes.dmp conforms to expected format
            with open(self.nodes_filename, 'r') as f:
                for tax_line in f:
                    tax_info = tax_line.split('|')[0:3]
                    tax_id = int(tax_info[0].strip())
                    tax_parent = int(tax_info[1].strip())
                    tax_rank = tax_info[2].strip()
                    tax_scientific_name = taxid2name[tax_id]['scientific_name']
                    tax_common_name = taxid2name[tax_id]['genbank_common_name']

                    if tax_id in self.taxonomy:
                        # We already inserted the current tax_id as a parent of another
                        self.taxonomy[tax_id].rank = tax_rank
                        self.taxonomy[tax_id].parent = tax_parent
                    else:
                        node = Node(
                            name=tax_scientific_name,
                            genbank_common_name=tax_common_name,
                            rank=tax_rank,
                            parent=tax_parent,
                            children=[])
                        self.taxonomy[tax_id] = node
                        self.leaves.add(tax_id)

                    if tax_parent in self.taxonomy:
                        self.taxonomy[tax_parent].children.append(tax_id)
                        if tax_parent in self.leaves:
                            self.leaves.remove(tax_parent)
                    else:
                        parent_node = Node(
                            name=taxid2name[tax_parent]['scientific_name'],
                            genbank_common_name=taxid2name[tax_parent]['genbank_common_name'],
                            rank=None,
                            parent=None,
                            children=[tax_id])
                        self.taxonomy[tax_parent] = parent_node

                    # Save the tax_id to it's corresponding rank set
                    if tax_rank in self.byranks:
                        self.byranks[tax_rank].add(tax_id)
                    else:
                        self.byranks[tax_rank] = set([tax_id])

        except FileNotFoundError:
            log.exception('Could not find the nodes file "{nodes_file}".'.format(nodes_file=self.nodes_filename))
            raise

        # Adjust the root (the root is tax_id=1, and its parent is also tax_id=1)
        root_children = self.taxonomy[1].children
        root_children.remove(1)
        self.taxonomy[1].parent = None
        self.taxonomy[1].children = root_children
        log.info("Taxonomy tree built.")


    def translate2taxid(self, scientific_names_list):
        """
        Will return the tax_ids for the scientific names listed in the input
        list. If no name can be found the value will be None. More than one
        tax_id may be found for any given scientific name - they will all be
        added to the list of tax_ids being returned for that scientific name.
        Returns:
        {<scientific_name>: [tax_id_1, tax_id_2]}
        """
        self._verify_list(scientific_names_list)
        tax_id_dict = {k: list() for k in scientific_names_list}

        if len(tax_id_dict) != len(scientific_names_list):
            log.warning('You entered duplicated names in the input list for translate2taxid.')

        for tax_id in self.taxonomy:
            if self.taxonomy[tax_id].name in tax_id_dict:
                name = self.taxonomy[tax_id].name
                tax_id_dict[name].append(tax_id)
            else:
                # continue search
                continue

        return tax_id_dict


    def _get_property(self, tax_id, property):
        """
        Internal function to fetch the value of a single property of a namedtuple in the taxonomy dictionary.
        Raises an exception if tax_id does not exist in the taxonomy tree.
        Raises an exception if the taxonomy tree isn't built yet.
        """
        if self.taxonomy:
            try:
                property_value = getattr(self.taxonomy[tax_id], property)
            except KeyError:
                log.exception('Could not find tax_id={tax_id} in the taxonomy tree.'.format(tax_id=tax_id))
                raise
            except AttributeError:
                log.exception('There is no such field ("{field}") in the namedtuple.'.format(field=property))
                raise
        else:
            log.exception('You have not built the taxonomy tree yet.')
            raise TaxonomyTreeException('You have not built the taxonomy tree yet.')

        return property_value

    def _verify_list(self, putative_list):
        """
        Internal helper function to check that input lists are indeed lists.
        """
        try:
            assert isinstance(putative_list, list)
        except AssertionError:
            log.exception('Input must be a list. You input "{input}", of type {input_type}'.format(
                input=putative_list, input_type=type(putative_list)))
            raise

    def get_name(self, tax_id_list):
        """
        Returns the names of the tax_ids in the input list.
        """
        self._verify_list(tax_id_list)
        name_dict = {}
        for tax_id in tax_id_list:
            name_dict[tax_id] = self._get_property(tax_id, 'name')
        return name_dict

    def get_common_name(self, tax_id_list):
        """
        Returns the genbank common names of the tax_ids in the input list.
        """
        self._verify_list(tax_id_list)
        name_dict = {}
        for tax_id in tax_id_list:
            name_dict[tax_id] = self._get_property(tax_id, 'genbank_common_name')
        return name_dict

    def get_children(self, tax_id_list):
        """
        Returns the direct descending children of each tax_id.
        """
        self._verify_list(tax_id_list)
        children_dict = {}
        for tax_id in tax_id_list:
            children_dict[tax_id] = self._get_property(tax_id, 'children')
        return children_dict

    def get_parent(self, tax_id_list):
        """
        Returns the parent of each tax_id.
        """
        self._verify_list(tax_id_list)
        parent_dict = {}
        for tax_id in tax_id_list:
            parent_dict[tax_id] = self._get_property(tax_id, 'parent')
        return parent_dict

    def get_distance(self, tax_id_1, tax_id_2):
        """
        Return the distance between two tax_ids. The distance is defined as
        the number of edges that need to be traversed to get from tax_id_1 to
        tax_id_2.

        Distance between a parent and child is 1, distance between two genera
        in the same family (where the family node is the direct parent of both
        genera) is 2, etc.

        All edges between two tax_ids are counted, so the distance between two
        ranks in one part of the tree can be different from that in another
        part of the tree (depending on tree structure).
        """

        def one_way_distance(tax_id_ancestor, tax_id):
            """
            Find the distance (number of steps) between the
            ancestor (tax_id_ancestor) and the taxon (tax_id).
            """

            # Lineage of the descendant tax_id (of which ancestor tax_id is part of)
            lineage = self.get_lineage([tax_id])[tax_id]

            # The indices of both tax_ids in the lineage
            ancestor_index = lineage.index(tax_id_ancestor)
            tax_id_index = lineage.index(tax_id)

            distance = tax_id_index - ancestor_index

            return distance

        distance = None

        # Extra calcs to check for distance from self.distances
        tax_id_small = min(tax_id_1, tax_id_2)
        tax_id_large = max(tax_id_1, tax_id_2)

        # self.distances is ordered... smallest tax_id always goes first
        if tax_id_small in self.distances:
            if tax_id_large in self.distances[tax_id_small]:
                distance = self.distances[tax_id_small][tax_id_large]
        else:
            self.distances[tax_id_small] = {}

        # Do we need to calculate the distance?
        if distance is None:

            # Lowest common ancestor
            lca = self.get_lca(tax_id_1, tax_id_2)

            # Sum of distances between both tax_ids and the LCA makes the total distance
            distance_1 = one_way_distance(lca, tax_id_1)
            distance_2 = one_way_distance(lca, tax_id_2)
            distance = distance_1 + distance_2

            # Save distance for faster response next time
            self.distances[tax_id_small][tax_id_large] = distance

        return distance

    def get_rank(self, tax_id_list):
        """
        Returns the rank of each tax_id.
        """
        self._verify_list(tax_id_list)
        rank_dict = {}
        for tax_id in tax_id_list:
            rank_dict[tax_id] = self._get_property(tax_id, 'rank')
        return rank_dict

    def get_rank_code(self, tax_id_list):
        """
        Returns the rank, rank code, and rank offset for each tax_id.
        For example:
        tax_id 314295 is rank 'superfamily'. That rank has no rank code in the
        original Kraken 2 reports (see translate_rank2code dict above). Same
        goes for all of the 'no rank' tax_ids. Instead, 314295 is considered to
        be an 'order' but at the depth of 4, i.e. 4 steps down from the tax_id
        of rank 'order' that is closes above it in the lineage. The rank code
        is therefore O, and the depth is 4. So the full rank code is O4.

        Returns a dict of namedtupes, one for each tax_id in the supplied list.
        """
        rank_dict = self.get_rank(tax_id_list)
        rank_code_dict = {}
        for tax_id in rank_dict:
            rank = rank_dict[tax_id]
            rank_code = ''
            current_node = tax_id

            # Find the rank code for this node or the one above
            while not rank_code:
                if rank in translate_rank2code:
                    rank_code = translate_rank2code[rank]
                elif current_node == 1:
                    # Special case for root, as it has rank 'no rank'
                    rank_code = 'R'
                else:
                    current_node = self.get_parent([current_node])[current_node]
                    rank = self.get_rank([current_node])[current_node]

            rank_depth = self.get_distance(current_node, tax_id)
            rank_name = self.get_rank([tax_id])[tax_id]

            rank_tuple = Rank(
                rank_name=rank_name,
                rank_code=rank_code,
                rank_depth=rank_depth)

            rank_code_dict[tax_id] = rank_tuple

        return rank_code_dict

    def get_node(self, tax_id_list):
        """
        Returns the node instances of the supplied tax_ids.
        """
        #TODO: Use this fnc in other fncs when getting nodes from self.taxonomy
        self._verify_list(tax_id_list)
        node_dict = {}

        if self.taxonomy:
            for tax_id in tax_id_list:
                try:
                    node = self.taxonomy[tax_id]
                except KeyError:
                    log.exception('Could not find tax_id={tax_id} in the taxonomy tree.'.format(tax_id=tax_id))
                    raise
                node_dict[tax_id] = node
        else:
            log.exception('You have not built the taxonomy tree yet.')
            raise TaxonomyTreeException('You have not built the taxonomy tree yet.')

        return node_dict

    def get_lineage(self, tax_id_list):
        """
        For each tax_id, returns the input tax_id and the tax_ids of its
        ancestors.
        """
        self._verify_list(tax_id_list)
        lineage_dict = {}

        for tax_id in tax_id_list:
            if tax_id in self.lineages:
                # Lineage has already been calculated, retrieve it
                lineage_dict[tax_id] = self.lineages[tax_id]
                continue

            lineage = [tax_id]
            node = self.get_node([tax_id])[tax_id]

            while node.parent:
                lineage.append(node.parent)
                node = self.get_node([node.parent])[node.parent]

            lineage.reverse()
            lineage_dict[tax_id] = lineage

            # Save lineage for faster response next time
            self.lineages[tax_id] = lineage

        return lineage_dict

    def get_clade(self, tax_id_list):
        """
        For each tax_id, returns all of the tax_ids of the clade rooted at the
        tax_id.

        returns: {tax_id#1: set(all tax_ids in node),
                  tax_id#2: set(all tax_ids in node)}
        """

        self._verify_list(tax_id_list)
        clade_dict = {}

        for tax_id in tax_id_list:
            node = self.get_node([tax_id])[tax_id]
            children_pool = set(node.children)
            clade = set([tax_id])
            clade.update(children_pool)

            while children_pool:
                try:
                    clade_taxon = children_pool.pop()
                except KeyError:
                    break
                else:
                    new_children = self.get_node([clade_taxon])[clade_taxon].children
                    clade.update(new_children)
                    children_pool.update(new_children)

            clade_dict[tax_id] = clade

        return clade_dict

    def get_leaves(self, tax_ids=[1]):
        """
        Returns a {tax_id: set(leaf_taxids)} mapping of leaf node tax_ids for
        the clades rooted at the tax_ids.
        """

        self._verify_list(tax_ids)
        clade_dict = {}

        def get_leaves_dfs(tax_id, clade_leaves, visited_nodes=None):
            if visited_nodes == None:
                visited_nodes = set()

            if tax_id not in visited_nodes:
                visited_nodes.add(tax_id)
                children = self.get_children([tax_id])[tax_id]
                if children:
                    for child in children:
                        get_leaves_dfs(child, clade_leaves, visited_nodes)
                else:
                    clade_leaves.add(tax_id)

                return clade_leaves

        for tax_id in tax_ids:
            clade_leaves = set()
            clade_leaves = get_leaves_dfs(tax_id, clade_leaves)
            clade_dict[tax_id] = clade_leaves

        return clade_dict

    def get_lca(self, tax_id_1, tax_id_2):
        """
        Get the tax_id of the lowest common ancestor (LCA) of two tax_ids.
        """
        lca = None

        # Extra calcs to check for lca from self.lca_mappings
        tax_id_small = min(tax_id_1, tax_id_2)
        tax_id_large = max(tax_id_1, tax_id_2)

        # self.lca_mappings is ordered... smallest tax_id always goes first
        if tax_id_small in self.lca_mappings:
            if tax_id_large in self.lca_mappings[tax_id_small]:
                lca = self.lca_mappings[tax_id_small][tax_id_large]
        else:
            self.lca_mappings[tax_id_small] = {}

        if lca is None:
            # Get lineages and convert to sets for fast operation
            lineages = self.get_lineage([tax_id_1, tax_id_2])
            lineage_1 = set(lineages[tax_id_1])
            lineage_2 = set(lineages[tax_id_2])

            # Get only the common tax_ids between the lineages of tax_id 1 and 2
            common_lineage = lineage_1.intersection(lineage_2)

            # The LCA will be the tax_id @ index (num(common_taxIDs) - 1)
            lca = lineages[tax_id_1][len(common_lineage) - 1]

            # Save LCA for faster response next time
            self.lca_mappings[tax_id_small][tax_id_large] = lca

        return lca

    def get_clade_rank_taxids(self, tax_ids, rank=None):
        """
        For each clade rooted at the input tax_ids, return all tax_ids that
        represent taxa at the supplied rank, or all ranks. For example:
        # get_clade_rank_taxids([1], 'phylum') -- returns all phyla in the whole tree
        # get_clade_rank_taxids([2, 9443], 'genus') -- returns all genera in the clades rooted at 'Bacteria' and 'Primates'
        # get_clade_rank_taxids([1]) -- returns all canonical ranks in the whole tree.
        """
        self._verify_list(tax_ids)

        canonical_ranks = translate_rank2code.values()
        canonical_rank_weights = {rank: weight for weight, rank in enumerate(['R'] + list(canonical_ranks))}
        clade_tax_rank_dict = {tax_id: dict() for tax_id in tax_ids}

        if rank:
            rank = translate_rank2code[rank]
        else:
            rank = canonical_ranks

        def dfs(tax_id, visited_nodes=None, tax_lvl_dict=None, wanted_ranks=None):
            """
            Fnc to recursively search the taxonomy tree in a depth-first
            fashion. Saves all tax_ids that are canonical (S/G/F etc) in
            tax_lvl_dict.
            """
            if visited_nodes is None:
                visited_nodes = set()

            if wanted_ranks is None:
                wanted_ranks = {rank in canonical_ranks}

            if tax_lvl_dict is None:
                tax_lvl_dict = {tax_lvl: set() for tax_lvl in wanted_ranks}

            if tax_id not in visited_nodes:
                visited_nodes.add(tax_id)

                taxonomy_rank = self.get_rank_code([tax_id])[tax_id]
                rank_code = taxonomy_rank.rank_code
                if taxonomy_rank.rank_depth == 0:
                    if rank_code in wanted_ranks:
                        tax_lvl_dict[rank_code].add(tax_id)

                rank_code_weight = canonical_rank_weights[rank_code]

                # Keep going down the tree only if there's a wanted rank below current rank
                if any([rank_code_weight < canonical_rank_weights[rank] for rank in wanted_ranks]):
                    children = self.get_children([tax_id])[tax_id]
                    for child in children:
                        _ = dfs(child, visited_nodes, tax_lvl_dict, wanted_ranks)

                return tax_lvl_dict

        for tax_id in tax_ids:
            tax_lvl_dict = dfs(tax_id, wanted_ranks=set(rank))
            clade_tax_rank_dict[tax_id] = tax_lvl_dict

        return clade_tax_rank_dict

    def get_siblings(self, tax_id):
        """
        NB! This fnc hasn't been extensively tested, use at own risk.

        This fnc is similar to get_clade_rank_taxids, but I think it should
        be faster.

        For a given tax_id X with any rank in ['S', 'G', 'F', 'O', 'C', 'P'],
        return all taxa with the same rank in the clade rooted at the parent
        of X. The parent is defined as the most recent ancestor of X that has
        a rank also in ['S', 'G', 'F', 'O', 'C', 'P'].

        For example, if the tax_id 3352 (Pinus taeda, a species) is submitted
        to the function, it will return all other species in the genus Pinus
        (3337). Conversely, if the genus Pinus (3337) is submitted, the
        function will return all genera in the family Pinaceae (3318).
        """
        # TODO: Test this more.
        # TODO: In line with other exposed functions in this class, it should take a list of taxids instead of a single one.

        tax_id_rank = self.get_rank_code([tax_id])[tax_id]
        rank = tax_id_rank.rank_code
        rank_codes = ['S', 'G', 'F', 'O', 'C', 'P']

        if tax_id_rank.rank_depth != 0:
            raise TaxonomyTreeException("Can only work with ranks of level {}.".format(rank_codes))

        def get_parent(tax_id):
            parent_rank_ok = False
            current_tax_id = tax_id
            while not parent_rank_ok:
                parent = self.get_parent([current_tax_id])[current_tax_id]
                taxonomy_rank = self.get_rank_code([parent])[parent]
                if taxonomy_rank.rank_code in rank_codes and taxonomy_rank.rank_depth == 0:
                    parent_rank_ok = True
                elif parent == 1:
                    parent_rank_ok = True
                else:
                    current_tax_id = parent

            return parent

        parent = get_parent(tax_id)

        visited_nodes = set()
        siblings = set()

        def dfs(tax_id, wanted_rank):
            if tax_id not in visited_nodes:
                visited_nodes.add(tax_id)
                taxonomy_rank = self.get_rank_code([tax_id])[tax_id]
                if taxonomy_rank.rank_code != wanted_rank:
                    children = self.get_children([tax_id])[tax_id]
                    for child in children:
                        dfs(child, wanted_rank)
                else:
                    siblings.add(tax_id)

        dfs(parent, rank)
        return siblings


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--nodes')
    parser.add_argument('--names')
    args = parser.parse_args()

    taxonomy_tree = TaxonomyTree(args.nodes, args.names)
