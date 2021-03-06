# SAFE TEAM
#
#
# distributed under license: CC BY-NC-SA 4.0 (https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.txt) #
#

from compiler_provenance.utils import __padAndFilter as padAndFilter
from asm_embedding.InstructionsConverter import InstructionsConverter
from asm_embedding.FunctionNormalizer import FunctionNormalizer
import json
from multiprocessing import Queue
from multiprocessing import Process
import networkx as nx
from networkx.readwrite import json_graph
import numpy as np
import random
from scipy import sparse
import sqlite3



class DatasetGenerator:

    def get_dataset(self, epoch_number):
        pass


class PairFactory(DatasetGenerator):

    def __init__(self, db_name, feature_type, dataset_type, json_asm2id, max_instructions, max_num_vertices, encoder, batch_size, flags=None):
        self.db_name = db_name
        self.feature_type = feature_type
        self.dataset_type = dataset_type
        self.encoder = encoder
        self.max_instructions = max_instructions
        self.max_num_vertices = max_num_vertices
        self.batch_dim = 0
        self.num_pairs = 0
        self.num_batches = 0
        self.flags=flags

        self.converter = InstructionsConverter(json_asm2id)
        self.normalizer = FunctionNormalizer(self.max_instructions)

        conn = sqlite3.connect(self.db_name)
        cur = conn.cursor()
        q = cur.execute("SELECT count(*) from " + self.dataset_type)
        count=int(q.fetchone()[0])
        n_chunk = int(count / batch_size) - 1

        self.num_batches = n_chunk
        conn.close()

    def remove_bad_acfg_node(self, g):
        nodeToRemove = []
        for n in g.nodes(data=True):
            f = n[1]['features']
            if len(f.keys()) == 0:
                nodeToRemove.append(n[0])
        for n in nodeToRemove:
            g.remove_node(n)
        return g

    def split(self,a, n):
        return [a[i::n] for i in range(n)]

    def get_node_matrix(self, nodes):

        if isinstance(nodes, int):
            print(nodes)

        num_node = len(nodes)
        node_matrix = np.zeros([num_node, 8])
        for i, n in enumerate(nodes):
            f = n[1]['features']

            if isinstance(f['constant'], int):
                node_matrix[i, 0] = f['constant']
            else:
                node_matrix[i, 0] = len(f['constant'])

            if isinstance(f['string'], int):
                node_matrix[i, 1] = f['string']
            else:
                node_matrix[i, 1] = len(f['string'])

            node_matrix[i, 2] = f['transfer']
            node_matrix[i, 3] = f['call']
            node_matrix[i, 4] = f['instruction']
            node_matrix[i, 5] = f['arith']
            node_matrix[i, 6] = f['offspring']
            node_matrix[i, 7] = f['betweenness']
        return node_matrix

    def get_data_from_acfg(self, g):
        g = self.remove_bad_acfg_node(g)
        if len(g.nodes) > 0:
            adj = nx.adjacency_matrix(g)
            node_matrix = self.get_node_matrix(g.nodes(data=True))
        else:
            adj = sparse.bsr_matrix(np.zeros([1, 1]))
            node_matrix = np.zeros([1, 8])
        lenghts = [8] * len(node_matrix)
        return adj, node_matrix, lenghts

    def get_data_from_cfg(self, cfg):
        adj = sparse.csr_matrix([1, 1])
        lenghts = []
        node_matrix = []

        try:
            adj = nx.adjacency_matrix(cfg)
            nodes = cfg.nodes(data=True)
            for i, n in enumerate(nodes):
                filtered = self.converter.convert_to_ids(n[1]['features'])
                lenghts.append(len(filtered))
                node_matrix.append(self.normalizer.normalize(filtered)[0])
        except:
            pass
        return adj, node_matrix, lenghts

    def async_chunker(self, epoch, batch_size, shuffle=True):
        self.num_pairs = 0

        conn = sqlite3.connect(self.db_name)
        cur = conn.cursor()
        q = cur.execute("SELECT id from " + self.dataset_type)
        ids = q.fetchall()
        ids = [ii[0] for ii in ids]

        data_len = len(ids)

        n_chunk = int(data_len / batch_size) - 1
        random.seed(17)
        self.num_batches = n_chunk
        lista_chunk=range(0,n_chunk)
        coda = Queue(maxsize=50)
        n_proc = 10
        listone = self.split(lista_chunk, n_proc)
        for i in range(0,n_proc):
            l = list(listone[i])
            p = Process(target=self.async_create_pair,args=((epoch, l, batch_size, coda, shuffle, self.encoder)))
            p.start()

        while coda.empty():
            pass
        for i in range(0, n_chunk):
            yield self.async_get_dataset(i, n_chunk, batch_size, coda, shuffle)

    def get_pair_from_db(self, epoch_number, chunk, number_of_functions, label_encoder):

        conn = sqlite3.connect(self.db_name)
        cur = conn.cursor()

        functions = []
        labels = []
        lenghts = []

        q = cur.execute("SELECT id FROM " + self.dataset_type)
        ids = q.fetchall()
        rng = random.Random(epoch_number)
        rng.shuffle(ids)
        data_len = len(ids)
        i = 0

        while i < number_of_functions:
            if chunk * int(number_of_functions) + i > data_len:
                break

            ii = ids[chunk * int(number_of_functions) + i]
            q = cur.execute("SELECT " + self.feature_type + " FROM " + self.feature_type + " WHERE id=?", ii)

            if self.feature_type == 'acfg':
                adj, node, lenghts0 = self.get_data_from_acfg(json_graph.adjacency_graph(json.loads(q.fetchone()[0])))
            elif self.feature_type == 'lstm_cfg':
                adj, node, lenghts0 = self.get_data_from_cfg(json_graph.adjacency_graph(json.loads(q.fetchone()[0])))

            functions.append([(adj, node)])
            lenghts.append(lenghts0)

            if self.flags is None or self.flags.class_kind == "CMP" or self.flags.class_kind == "FML":
                query_str = "SELECT  compiler FROM functions WHERE id=?"
            elif self.flags.class_kind == "CMPOPT":
                query_str = "SELECT  compiler,optimization FROM functions  WHERE id=?"
            elif self.flags.class_kind == "OPT":
                query_str = "SELECT  optimization FROM functions  WHERE id=?"

            q = cur.execute(query_str, ii)
            q_compiler = q.fetchone()

            if self.flags.class_kind == "CMPOPT":
                compiler = q_compiler[0] + '-' + q_compiler[1]
            elif self.flags.class_kind == "FML":
                compiler = str(q_compiler[0]).split('-')[0]
            else:
                compiler = q_compiler[0]

            encoded = label_encoder.transform([compiler])
            labels.append(encoded)
            i += 1

        if self.feature_type == 'acfg':
            pairs, labels, output_len = padAndFilter(functions, labels, [[[1]]]*len(functions), self.max_num_vertices)
            output_len = [[1]]

        elif self.feature_type == 'lstm_cfg':
            pairs, labels, output_len = padAndFilter(functions, labels, lenghts, self.max_num_vertices)

        return pairs, labels, output_len

    def async_create_pair(self, epoch, n_chunk, number_of_functions, q, shuffle, encoder):

        for i in n_chunk:
            pairs, y, lenghts = self.get_pair_from_db(epoch, i, number_of_functions, encoder)
            assert (len(pairs) == len(y))
            n_samples=len(pairs)
            len1 = []
            for l in lenghts:
                len1.append(l[0])
            adj1 = []
            nodes1 = []
            for p in pairs:
                adj1.append(p[0][0])
                nodes1.append(p[0][1])
            y_ = []
            for yy in y:
                y_.append(yy[0])

            for i in range(0, n_samples, number_of_functions):
                upper_bound = min(i + number_of_functions, n_samples)

                ret_adj = adj1[i:upper_bound]
                ret_nodes = nodes1[i:upper_bound]
                ret_len = len1[i:upper_bound]
                ret_y = y_[i:upper_bound]

            q.put((ret_adj,ret_nodes,ret_y,ret_len), block=True)

    def async_get_dataset(self, chunk, n_chunk, number_of_pairs, q, shuffle):
        item = q.get()
        n_samples = len(item[0])
        self.batch_dim = n_samples
        self.num_pairs += n_samples
        return item[0], item[1], item[2], item[3]

