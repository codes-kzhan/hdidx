#!/usr/bin/env python
# coding: utf-8

#########################################################################
#########################################################################

"""
   File Name: indexer.py
      Author: Wan Ji
      E-mail: wanji@live.com
  Created on: Tue Nov  4 09:07:38 2014 CST
"""
DESCRIPTION = """
"""

import cPickle as pickle
import logging

import numpy as np

from util import kmeans, pq_kmeans_assign, pq_knn
from distance import distFunc
from storage import createStorage

import _cext as cext


class Indexer(object):
    class IdxData(object):
        pass

    def __init__(self):
        self.ERR_INSTAN = "Instance of `Indexer` is not allowed!"
        self.ERR_UNIMPL = "Unimplemented method!"
        pass

    def __del__(self):
        pass

    def build(self, vals=None, labels=None):
        """
        Build the indexer based on given training data
        """
        raise Exception(self.ERR_INSTAN)

    def load(self, path):
        """
        Load indexer information from file
        """
        with open(path, 'rb') as pklf:
            self.idxdat = pickle.load(pklf)

    def save(self, path):
        """
        Save the information related to the indexer itself
        """
        with open(path, 'wb') as pklf:
            pickle.dump(self.idxdat, pklf, protocol=2)

    def set_storage(self, storage_type='mem', storage_parm=None):
        """
        Set up the backend storage engine
        """
        raise Exception(self.ERR_INSTAN)

    def add(self, vals, keys):
        """
        Add one or more items to the indexer
        """
        raise Exception(self.ERR_INSTAN)

    def remove(self, keys):
        """
        Remove one or more items from the indexer
        """
        raise Exception(self.ERR_INSTAN)

    def search(self, querys, topk=None, thresh=None):
        """
        Search in the indexer for `k` nearest neighbors or
        neighbors in a distance of `thresh`
        """
        raise Exception(self.ERR_INSTAN)


class PQIndexer(Indexer):
    def __init__(self):
        Indexer.__init__(self)

    def __del__(self):
        pass

    def build(self, pardic=None):
        # training data
        vals = pardic['vals']
        # the number of subquantizers
        nsubq = pardic['nsubq']
        # the number bits of each subquantizer
        nsubqbits = pardic.get('nsubqbits', 8)
        # the number of items in one block

        blksize = pardic.get('blksize', 16384)

        # vector dimension
        dim = vals.shape[1]
        # dimension of the subvectors to quantize
        dsub = dim / nsubq
        # number of centroids per subquantizer
        ksub = 2 ** nsubqbits

        """
        Initializing indexer data
        """
        # idxdat = Indexer.IdxData()
        # idxdat.nsubq = nsubq
        # idxdat.ksub = ksub
        # idxdat.dsub = dsub
        # idxdat.centroids = [None for q in range(nsubq)]

        idxdat = dict()
        idxdat['nsubq'] = nsubq
        idxdat['ksub'] = ksub
        idxdat['dsub'] = dsub
        idxdat['blksize'] = blksize
        idxdat['centroids'] = [None for q in range(nsubq)]

        logging.info("Building codebooks in subspaces - BEGIN")
        for q in range(nsubq):
            logging.info("\tsubspace %d/%d:" % (q, nsubq))
            vs = np.require(vals[:, q*dsub:(q+1)*dsub], requirements='C')
            idxdat['centroids'][q] = kmeans(vs, ksub, niter=100)
        logging.info("Building codebooks in subspaces - DONE")

        self.idxdat = idxdat

    def set_storage(self, storage_type='mem', storage_parm=None):
        self.storage = createStorage(storage_type, storage_parm)

    def add(self, vals, keys=None):
        num_vals = vals.shape[0]
        if keys is None:
            num_base_items = self.storage.get_num_items()
            keys = range(num_base_items, num_base_items + num_vals)

        dsub = self.idxdat['dsub']
        nsubq = self.idxdat['nsubq']
        centroids = self.idxdat['centroids']

        blksize = self.idxdat.get('blksize', 16384)
        start_id = 0
        for start_id in range(0, num_vals, blksize):
            cur_num = min(blksize, num_vals - start_id)
            print "%8d/%d: %d" % (start_id, num_vals, cur_num)

            codes = np.zeros((cur_num, nsubq), np.uint8)
            for q in range(nsubq):
                vsub = vals[start_id:start_id+cur_num, q*dsub:(q+1)*dsub]
                codes[:, q] = pq_kmeans_assign(centroids[q], vsub)
            # self.storage.add(codes, keys[start_id:start_id+cur_num])
            self.storage.add(codes, keys)

        # codes = np.zeros((num_vals, nsubq), np.uint8)
        # for q in range(nsubq):
        #     vsub = vals[:, q*dsub:(q+1)*dsub]
        #     codes[:, q] = pq_kmeans_assign(centroids[q], vsub)
        # self.storage.add(codes, keys)

    def remove(self, keys):
        raise Exception(self.ERR_UNIMPL)

    def search(self, querys, topk=None, thresh=None):
        nq = querys.shape[0]

        dsub = self.idxdat['dsub']
        nsubq = self.idxdat['nsubq']
        ksub = self.idxdat['ksub']
        centroids = self.idxdat['centroids']

        distab = np.zeros((nsubq, ksub), np.single)
        dis = np.zeros((nq, topk), np.single)
        ids = np.zeros((nq, topk), np.int)

        for qid in range(nq):
            # pre-compute the table of squared distance to centroids
            for q in range(nsubq):
                vsub = querys[qid:qid+1, q*dsub:(q+1)*dsub]
                distab[q:q+1, :] = distFunc['euclidean'](
                    centroids[q], vsub)

            # add the tabulated distances to construct the distance estimators
            idsquerybase, disquerybase = self.sumidxtab(distab)
            cur_ids = pq_knn(disquerybase, topk)

            ids[qid, :] = idsquerybase[cur_ids]
            dis[qid, :] = disquerybase[cur_ids]

        return ids, dis

    def sumidxtab(self, D):
        """
        Compute distance to database items based on distances to centroids.
            D: nsubq x ksub
        """

        num_base_items = self.storage.get_num_items()

        dis = np.zeros(num_base_items)
        ids = []

        start_id = 0
        for keys, blk in self.storage:
            cur_num = blk.shape[0]
            # dis[start_id:start_id+cur_num] = self.sumidxtab_core(D, blk)
            dis[start_id:start_id+cur_num] = cext.sumidxtab_core(D, blk)
            start_id += cur_num
            ids += keys

        return np.array(ids), dis

    @classmethod
    def sumidxtab_core(cls, D, blk):
        # return 0
        return [sum([D[j, blk[i, j]] for j in range(D.shape[0])])
                for i in range(blk.shape[0])]
