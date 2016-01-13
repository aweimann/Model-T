#params:
#indices for target variables (phenotypes)
#out
#import liblinear as ll
#from liblinear import *
import numpy as np
import pandas as ps
import sklearn.svm as svm
from joblib import Parallel, delayed, load, dump
#TODO use the sklearn version of joblib instead to remove the direct joblib dependency
from operator import itemgetter
import math
import random
random.seed(1)
#initialize with seed
#TODO put this into a class and use random state from gideon script
random_nested_cv = random.Random()
random_nested_cv.seed(1)
import cv_rec_helper as crh 
import sys
import itertools
import sklearn.preprocessing as preprocessing
import copy_reg
import types
import os


def _pickle_method(method):
        # Author: Steven Bethard
        # http://bytes.com/topic/python/answers/552476-why-cant-you-pickle-instancemethods
        func_name = method.im_func.__name__
        obj = method.im_self
        cls = method.im_class
        cls_name = ''
        if func_name.startswith('__') and not func_name.endswith('__'):
            cls_name = cls.__name__.lstrip('_')
        if cls_name:
            func_name = '_' + cls_name + func_name
        return _unpickle_method, (func_name, obj, cls)

def _unpickle_method(func_name, obj, cls):
    # Author: Steven Bethard
    # http://bytes.com/topic/python/answers/552476-why-cant-you-pickle-instancemethods
    for cls in cls.mro():
        try:
            func = cls.__dict__[func_name]
        except KeyError:
            pass
        else:
            break
        return func.__get__(obj, cls)

copy_reg.pickle(types.MethodType, _pickle_method, _unpickle_method)

class nested_cv:

    def __init__(self, likelihood_params, parsimony_params, do_normalization, is_rec_based, is_phypat_and_rec, n_jobs, inverse_feats, config, perc_feats, perc_samples, model_out, cv_outer, resume):
        self.config = config
        self.likelihood_params = likelihood_params
        self.parsimony_params = parsimony_params
        self.do_normalization = do_normalization
        self.is_rec_based = is_rec_based
        self.n_jobs = n_jobs
        self.inverse_feats = inverse_feats
        self.is_phypat_and_rec = is_phypat_and_rec
        self.perc_feats = perc_feats
        self.perc_samples = perc_samples
        self.model_out = model_out
        self.cv_outer = cv_outer
        self.resume = resume

    def transf_from_probs(self, x, y):
        """create a series of sample specific weights according to the probabilities"""
        y_extd = [] 
        x_index = []
        for yi in range(len(y)):
            if y[yi] == 0 or y[yi] == -1:
                y_extd.append(-1)
                x_index.append(yi)
            else:
                y_extd.append(1)
                x_index.append(yi)
                if "extend" in self.likelihood_params:
                    y_extd.append(-1)
                    x_index.append(yi)
        w = []
        for yi in range(len(y)):
            if y[yi] == 0 or y[yi] == -1:
                w.append(1)
            else:   
                w.append(y[yi])
                if "extend" in self.likelihood_params:
                    w.append(1 - y[yi])
        return x.iloc[x_index,], ps.Series(y_extd), ps.Series(w)
    
    def normalize(self, array):
        """normalize a feature matrix"""
        scaler = preprocessing.StandardScaler(with_mean = True, with_std = True).fit(array)
        transformed = ps.DataFrame(data = scaler.transform(array), index = array.index, columns = array.columns)
        return transformed, scaler
        #return  scaler, cs
    
    
    def get_pfam_names_and_descs(self, feats):
        """map features to their pfam names and descriptions via a mapping file"""
        pfam_f = open(self.config['id2pfam2desc'], "r")
        id2pf = {}
        ls = pfam_f.readlines()
        for i in range(len(ls)):
            elems = ls[i].strip().split("\t")
            #mapping counts from 1 
            id2pf[int(elems[0]) - 1] = (elems[1],elems[2])
        pfam_f.close()
        return id2pf
    
    @staticmethod 
    def confusion_m(y,y_pred):
        """get confusion matrix TN FP /n FN TP"""
        TP = (y[y == 1] == y_pred[y == 1]).sum()
        FN = (y[y == 1] != y_pred[y == 1]).sum()
        TN = (y[y == -1] == y_pred[y == -1]).sum()
        FP = (y[y == -1] != y_pred[y == -1]).sum()
        return ps.np.array([TN, FP, FN, TP])

    @staticmethod 
    def recall_pos(y,y_pred):
        """compute recall of the positive class"""
        return (y[y == 1] == y_pred[y==1]).sum()/float((y==+1).sum())
    
    @staticmethod 
    def recall_pos_conf(conf):
        """compute recall of the positive class"""
        TN, FP, FN, TP = conf
        if (TP + FN) == 0:
            float('nan') 
        return TP/float(TP+FN)
    
    @staticmethod 
    def recall_neg_conf(conf):
        """compute recall of the positive class"""
        TN, FP, FN, TP = conf
        if (TN + FP) == 0:
            return float('nan')
        return TN/float(TN+FP)

    @staticmethod 
    def recall_neg(y, y_pred):
        """compute recall of the negative class"""
        return (y[y == -1] == y_pred[y==-1]).sum()/float((y==-1).sum())
    
    @staticmethod 
    def precision(y, y_pred):
        """compute precision"""
        TP = (y[y == 1] == y_pred[y == 1]).sum()
        FP = (y[y == -1] != y_pred[y == -1]).sum()
        if (TP + FP) == 0:
            return 0
        return TP / float(TP + FP)   
    @staticmethod 
    def precision_conf(conf):
        """compute precision"""
        TN, FP, FN, TP = conf
        if (TP + FP) == 0:
            return float('nan')
        return TP / float(TP + FP)

    @staticmethod
    def bacc(pos_acc, neg_acc):
        """compute balanced accuracy"""
        return float(pos_acc + neg_acc)/2
    
    def balance_weights(self, weights, y):
        """balance weights between pos/neg class in phyletic pattern and in the reconstruction based samples """
        weights.index = y.index
        #print weights
        weights[y == -1]  = weights[y == -1] / weights[y == -1].sum() * 40 
        weights[y == 1]  = weights[y == 1] / weights[y == 1].sum() * 40
        #print y
        #print weights
        return weights
    
    
    def cv(self, x_train, y_train, xp_train, yp_train, x_test , C, no_classifier = 10):
        """train model on given training features and target and return the predicted labels for the left out samples"""
        if not self.likelihood_params is None and "continuous_target" in self.likelihood_params:
            #transform x and y
            x_train, y_train, w = self.transf_from_probs(x_train, y_train, self.likelihood_params)
        else:
            w = ps.Series(np.ones(shape = len(y_train)) )
        #set y negative labels to -1
        y_train.index = x_train.index
        w.index = x_train.index
        y_train_t = y_train.copy() 
        y_train_t[y_train_t == 0] = -1
        yp_train_t = yp_train.copy() 
        yp_train_t[yp_train_t == 0] = -1
        predictor = svm.LinearSVC(C=C)
        predictor.set_params(**self.config['liblinear_params'])
        #check if we are in vanilla linear SVM and set no_classifiers to 1 if so or in subspace mode 
        if self.perc_feats == 1.0 and self.perc_samples == 1.0:
            no_classifier = 1
        all_preds = ps.DataFrame(np.zeros(shape = (x_test.shape[0], no_classifier)))
        for i in range(no_classifier):
            #select a subset of features for classification
            sample_feats = sorted(random.sample(x_train.columns, int(math.floor(x_train.shape[1] * self.perc_feats))))
            #select a subset of samples for classification
            sample_samples = sorted(random_nested_cv.sample(x_train.index, int(math.floor(x_train.shape[0] * self.perc_samples))))
            sample_samples_p = sorted(random_nested_cv.sample(xp_train.index, int(math.floor(xp_train.shape[0] * self.perc_samples))))
            #reduce feature space to the selected features
            x_train_sub = x_train.loc[sample_samples, sample_feats].copy()
            y_train_t_sub = y_train_t.loc[sample_samples]
            yp_train_t_sub = yp_train_t.loc[sample_samples_p]
            #print y_train_t_sub, yp_train_t_sub, w_sub
    
            if self.is_phypat_and_rec:
                #reduce xp feature space to the selected features
                #EXPERIMENTAL DISCARD NEGATIVE reconstruction labels
                #index_vector = (y_train_t != -1) | (w != 1)
                #print y_train_t
                #print w
                #print index_vector
                #print x_train_sub.shape
                #index_vector.index = x_train_sub.index
                #x_train_sub = x_train_sub[index_vector]
                #index_vector.index = y_train_t.index
                #y_train_t = y_train_t[index_vector]
                #w = w[index_vector]
                #END EXPERIMENTAL DISCARD NEGATIVE reconstruction labels
                xp_train_sub = xp_train.loc[sample_samples_p, sample_feats]
                #EXPERIMENTAL BALANCE WEIGHTS
                #sample_weight = ps.concat([balance_weights(w, y_train_t), balance_weights(ps.Series(np.ones(len(yp_train_t))), yp_train_t)])
                #print sample_weight.sum(), "sample weights total"
                #END EXPERIMENTAL BALANCE WEIGHTS
                X = ps.concat([x_train_sub, xp_train_sub], axis = 0)
                if self.inverse_feats:
                    #add inverse features
                    X = ps.concat([X,1-X], axis = 1) 
                if self.do_normalization:
                    X, scaler = self.normalize(X)
                y = ps.concat([y_train_t_sub, yp_train_t_sub], axis = 0)
                predictor.fit(X = X, y = y)
            else: 
                if self.inverse_feats:
                    #add inverse features
                    x_train_sub = ps.concat([x_train_sub, 1 - x_train_sub], axis = 1) 
                if self.do_normalization:
                    x_train_sub, scaler = self.normalize(x_train_sub)
                predictor.fit(x_train_sub, y_train_t_sub)
            x_test_sample = x_test.loc[:, sample_feats].copy()
            if self.inverse_feats:
                #add inverse features to test sample
                x_test_sample = ps.concat([x_test_sample, 1 - x_test_sample], axis = 1) 
            if self.do_normalization:
                x_test_sample = ps.DataFrame(data = scaler.transform(x_test_sample), index = x_test_sample.index, columns = x_test_sample.columns)
            all_preds.iloc[:, i]  = predictor.predict(x_test_sample)
        #do majority vote to aggregate predictions
        aggr_preds = all_preds.apply(lambda x: 1 if sum(x == 1) > sum(x == -1) else -1, axis = 1).astype('int')
        return aggr_preds 
    
    def get_training_and_testing_mapping(self, train_pt_edges, all_pt_edges):
        """map phenotype edges that are not in the full reconstruction and thus must have been joined"""
        train2all = {}
        for e in train_pt_edges:
            nodes = e.split("_")
            for e2 in all_pt_edges:
                nodes2 = e2.split("_")
                maps = True
                for n2 in nodes2:
                    if n2 not in nodes:
                        maps = False
                if maps: 
                    if not e in train2all: 
                        train2all[e] = [e2]
                    else: 
                        train2all[e].append(e2)
        test_edges = all_pt_edges.intersection(set(itertools.chain.from_iterable(train2all.values())))
        return train2all, test_edges
    
    def join_edges(self, x_r, train2all, likelihood_params, parsimony_params):
        """aggregate edges in the full reconstruction matrix to fit the joint edges in train2all"""
        df = ps.DataFrame()
        for e in train2all:
            #aggregate rows by summing up
            if not self.likelihood_params is None:
                if not 'gt_probs' in self.likelihood_params:
                    cs = x_r.loc[train2all[e], :].sum(axis=0)
                    df = ps.concat([df, cs], axis = 1)
                else:
                    #sum up the probabilities
                    cs = np.zeros(shape = x_r.shape[1])
                    for i in range(x_r.loc[train2all[e], :].shape[0]):
                        #print "before aggregation", cs 
                        #print "being aggregated", x_r.loc[train2all[e], :].iloc[i, :]
                        cs = cs + (1 - cs) * x_r.loc[train2all[e], :].iloc[i, :]
                        #print "after aggregation", cs
                    df = ps.concat([df, ps.Series(cs)], axis = 1)
            else: 
                raise Exception("parsimony case not yet implemented")    
        #make sure there are no entries in the summed up likelihood matrix other than 0 and 1
        if not self.likelihood_params is None and 'gt_probs' in self.likelihood_params:
            df[x_r > 0] = 1
        #get df back into samples x features shape 
        df.columns = train2all.keys()
        return df.T
    
    
    def get_rec_samples(self, x_r, y_r, yp_train, pt_out, ofold, ifold):
        """compute the training samples by running parsimony/likelihood reconstruction for the pt for the training set without the held out samples"""
        #retrieve matrix like:
        #N1_N4 0 
        #N2_44 1
        if not self.parsimony_params is None:
            m = crh.reconstruct_pt_parsimony(yp_train, self.model_out, self.config, self.parsimony_params)
        else:
            m = crh.reconstruct_pt_likelihood(yp_train, self.model_out, self.config, self.likelihood_params, pt_out, ofold, ifold)
        all_pt_edges = set(x_r.index)
        train_pt_edges = set(m.index)
        #print len(all_pt_edges), "anzahl aller reconstruction samples"
        #print len(train_pt_edges), "anzahl aller reconstruction samples ohne test samples"
        #print yp_train.index.values, "training samples"
        #all edges only present in the full reconstruction
        df1 = all_pt_edges.difference(train_pt_edges)
        #print "held out edges including not yet joined edges", df1
        #all edges that are only in the training reconstruction and thus must have been joined
        df2 = train_pt_edges.difference(all_pt_edges)
        #print "edges that are only found in the training reconstruction", df2
        #print "training edges", train_pt_edges
        #map training phenotype edges which don't have a 1:1 equivalent in the all_pt_edges 
        train2all, test_edges = self.get_training_and_testing_mapping(df2, df1)
        #print "mapping from edges only in the training reconstruction to edges in the full reconstruction", train2all
        #print "held out edges from get training and testing mapping", test_edges
        #get the corresponding samples in the full reconstruction matrix and aggregate them
        joint_x_r = self.join_edges(x_r, train2all, self.likelihood_params, self.parsimony_params)
        #add the y labels to those rows
        joint_y_r = m.loc[train2all, :].iloc[:, 0] 
        #add all the other training samples
        training_edges = all_pt_edges.intersection(train_pt_edges)
        #print x_r.loc[training_edges,:].shape
        #print joint_x_r.shape, joint_x_r.index
        x_r_train = ps.concat([x_r.loc[training_edges,:], joint_x_r], axis = 0)
        #print "x_r_train shape", x_r_train.shape
        #print "x_r_train index", x_r_train.index
        y_r_train = ps.concat([m.loc[training_edges, :].iloc[:,0], joint_y_r], axis = 0)
        y_r_train[y_r_train == 2] = 1
        #print "y_r_train shape", y_r_train.shape
        #replace negative labels with -1
        x_r_test = x_r.loc[test_edges,]
        y_r_test = y_r.loc[test_edges,]
        return x_r_train, y_r_train, x_r_test, y_r_test
    
    
    def setup_folds(self, x, y, cv, x_p, y_p, pt_out, ofold = None):
        """prepare all combinations of training and test folds, either for the standard phyletic pattern or the likelihood / parsimony reconstruction case or for the combined case"""
        pfolds = self.setup_folds_phypat(x_p, y_p, cv)
        if not self.is_rec_based:
            #standard phyletic pattern cv
            return pfolds
        else:
            folds = []
            for i in range(len(pfolds)):
                xp_train, yp_train, xp_test, _, _, _, _ = pfolds[i]
                if ofold is None:
                    x_train, y_train, x_test, y_test  = self.get_rec_samples(x, y, yp_train, pt_out, ofold = i, ifold = None)
                else:
                    x_train, y_train, x_test, y_test  = self.get_rec_samples(x, y, yp_train, pt_out, ofold = ofold, ifold = i)
                folds.append((x_train, y_train, x_test, y_test,  xp_train, yp_train, xp_test))
        return folds
    
    def setup_folds_phypat(self, x, y, cv):
        """divide the samples into cv folds and return a list of index lists"""
        print "y length", len(y)
        fold_l = len(y)/cv
        print "fold_l", fold_l
        #number of folds with one more element
        fold_l_e = len(y)%cv
        print "fold_l_e", fold_l_e
        #list of folds lists
        folds = []
        for i in range(fold_l_e):
            index = range(i*(fold_l+1), i*(fold_l+1)+fold_l+1)
            #print x.shape, "phyletic pattern shape before removing test samples"
            x_train = x.drop(x.index[index])
            #print x_train.shape, "phyletic pattern shape after removing test samples"
            x_test = x.iloc[index,:]
            y_train = y.drop(y.index[index])
            folds.append((x_train, y_train, x_test, None, x_train, y_train, x_test))
        n_e = fold_l_e * (fold_l+1)
        for i in range(cv - fold_l_e):
            index = range(n_e + fold_l*i, n_e + fold_l*i + fold_l)
            x_train = x.drop(x.index[index])
            x_test = x.iloc[index,:]
            y_train = y.drop(y.index[index])
            folds.append((x_train, y_train, x_test, None, x_train, y_train, x_test))
        return folds
    
    
    
    def outer_cv(self, x, y, x_p, y_p, pt_out, cv_inner = None):
        """do a cross validation with cv_outer folds
        if only cv_outer is given do a cross validation for each value of the paramter C given
        if cv_inner is given, do a nested cross validation optimizing the paramter C in the inner loop"""
        #do n fold cross validation
        #number of elements in each invidual fold
        #print y_p.index, y.index
        if not cv_inner is None:
            ofolds = self.setup_folds(x, y, self.cv_outer, x_p, y_p, pt_out)
            print "outer folds set up"
            ocv_preds = []
            for i in range(len(ofolds)):
                x_train, y_train, x_test, y_test,  xp_train, yp_train, xp_test  = ofolds[i]
                ifolds = self.setup_folds(x_train, y_train, cv_inner, xp_train, yp_train, pt_out, i)
                print "inner folds set up"
                #dump(ofolds, "%s/%s_%s_ifolds.pickled" % (self.model_out, self.pt_out, i))
                all_preds = ps.DataFrame(np.zeros(shape=[len(yp_train), len(self.config['c_params'])]))
                all_preds.columns = self.config['c_params']
                all_preds.index = yp_train.index
                #do inner cross validation
                preds = []
                for pred in Parallel(n_jobs=self.config['n_jobs'])(delayed(self.cv)(x_train_train, y_train_train, xp_train_train, yp_train_train, xp_train_test, self.config['c_params'][j])
                        for(x_train_train, y_train_train, x_train_test, y_train_test,  xp_train_train, yp_train_train, xp_train_test, j) 
                            in ((x_train_train, y_train_train, x_train_test, y_train_test,  xp_train_train, yp_train_train, xp_train_test, j) for x_train_train, y_train_train, x_train_test, y_train_test,  xp_train_train, yp_train_train, xp_train_test in ifolds for j in range(len(self.config['c_params'])))):
                    preds.append(list(pred))
                preds_ordered = [[] for i in range(len(self.config['c_params']))]
                for i in range(len(preds)):
                    preds_ordered[i % len(self.config['c_params'])] += preds[i]
                all_preds = ps.DataFrame(np.array(preds_ordered), index = self.config['c_params'], columns = yp_train.index).T
                #determine C param with the best accuracy
                #copy yp train and change the negative labels from 0 to -1
                yp_t_t = yp_train.copy()
                yp_t_t[yp_t_t == 0] = -1
                baccs = [self.bacc(self.recall_pos(yp_t_t, all_preds.iloc[:,j]), self.recall_neg(yp_t_t, all_preds.iloc[:,j])) for j in range(len(self.config['c_params']))]
                print baccs, "baccs"
                c_opt = self.config['c_params'][np.argmax(np.array(baccs))]
                print c_opt
                #use that C param to train a classifier to predict the left out samples in the outer cv
                p = self.cv(x_train, y_train, xp_train, yp_train, xp_test, c_opt,  no_classifier = 10)
                ocv_preds += list(p)
            return ocv_preds
        
        folds = self.setup_folds(x, y, self.cv_outer, x_p, y_p, pt_out)
        #for i in folds:
        #    for j in i: 
        #print j.shape
        all_preds = np.zeros(shape=[len(y_p), len(self.config['c_params'])])
        #TODO account for the case when folds exceeds number of samples
        for j in range(len(self.config['c_params'])):
            preds = []
            for pred in Parallel(n_jobs= self.config['n_jobs'])(delayed(self.cv)(x_train, y_train, xp_train, yp_train, xp_test, self.config['c_params'][j])
                    for  x_train, y_train, x_test, y_test,  xp_train, yp_train, xp_test in folds):
                preds += list(pred)
            all_preds[:,j] = preds
        return all_preds
        
        
    def majority_feat_sel(self, x, y, x_p, y_p, all_preds, k, pt_out, no_classifier = 10):
        """determine the features occuring in the majority of the k best models"""
        #sanity check if number of classifiers selected for majority feature selection exceeds the number of c params
        if k > len(self.config['c_params']):
            sys.exit("number of selected classifiers for feature selection (%s) exceeds the number of c params (%s)" %(k, len(self.config['c_params'])))
        #retrieve weights from likelihood_params
        if not self.likelihood_params is None and "continuous_target" in self.likelihood_params:
            #transform x and y
            x, y, w = self.transf_from_probs(x,y)
        else:
            w = ps.Series(np.ones(shape = len(y)) )
        #determine the k best classifiers
        all_preds.index = y_p.index
        x.columns = x_p.columns
        y.index = x.index
        w.index = x.index
        y_p_t = y_p.copy()
        #make sure the negative class label -1 instead of 0
        y_p_t[y_p_t == 0] = -1
        y_t = y.copy()
        y_t[y_t == 0] = -1
        #compute balanced accuracy, positive and negative recall
        baccs = [self.bacc(self.recall_pos(y_p_t, all_preds.iloc[:,j]), self.recall_neg(y_p_t, all_preds.iloc[:,j])) for j in range(len(self.config['c_params']))]
        recps = [self.recall_pos(y_p_t, all_preds.iloc[:,j]) for j in range(len(self.config['c_params']))]
        recns = [self.recall_neg(y_p_t, all_preds.iloc[:,j]) for j in range(len(self.config['c_params']))]
        baccs_s = sorted(((baccs[i], recps[i], recns[i], self.config['c_params'][i]) for i in range(len(self.config['c_params']))), key=itemgetter(0), reverse=True )
        predictors = []
        #check if we are in vanilla linear SVM and set no_classifiers to 1 if so or in subspace mode 
        if self.perc_feats == 1.0 and self.perc_samples == 1.0:
            no_classifier = 1
        models = np.zeros(shape=(x.shape[1], k * no_classifier))
        #EXPERIMENTAL DISCARD NEGATIVE reconstruction labels
        #index_vector = (y_t != -1) | (w != 1)
        #print y_t
        #print w
        #print index_vector
        #print x.shape
        #index_vector.index = x.index
        #x = x[index_vector]
        #index_vector.index = y_t.index
        #y_t = y_t[index_vector]
        #w = w[index_vector]
        #END EXPERIMENTAL
        for i in range(k):
            predictor = svm.LinearSVC(C=baccs_s[i][3])
            predictor.set_params(**self.config["liblinear_params"])
            #sample A features and B samples if the corresponding options are set
            sample_feats = sorted(random.sample(x.columns, int(math.floor(x.shape[1] * self.perc_feats))))
            sample_samples_p = sorted(random_nested_cv.sample(x_p.index, int(math.floor(x_p.shape[0] * self.perc_samples))))
            sample_samples = sorted(random_nested_cv.sample(x.index, int(math.floor(x.shape[0] * self.perc_samples))))
            #train the full model with the k best models
            for l in range(no_classifier):
                x_sub = x.loc[sample_samples, sample_feats]
                y_t_sub = y_t.loc[sample_samples]
                if self.is_phypat_and_rec:
                    x_p_sub = x_p.loc[sample_samples_p, sample_feats]
                    y_p_t_sub = y_p_t.loc[sample_samples_p]
                    X = ps.concat([x_sub, x_p_sub], axis = 0)
                    #add inverse features if the corresponding option is set
                    if self.inverse_feats:
                        X = ps.concat([X, 1 - X], axis = 1)
                    #normalize if the corresponding option is set
                    if self.do_normalization:
                        X, _ = self.normalize(X)
                    #predictor.fit(X, ps.concat([y_t, y_p_t], axis = 0), sample_weight = ps.concat([balance_weights(w, y_t), balance_weights(ps.Series(np.ones(shape = len(y_p))), y_p_t)]))
                    predictor.fit(X, ps.concat([y_t_sub, y_p_t_sub], axis = 0))
                else:
                    if self.inverse_feats:
                        #add inverse features
                        x_sub =  ps.concat([x_sub, x_sub], axis = 1)
                    #normalize if the corresponding option is set
                    if self.do_normalization:
                        x_sub, _ = self.normalize(x_sub)
                    predictor.fit(x_sub, y_t_sub)
                    #save the model
                #add inverse features if the corresponding option is set
                if self.inverse_feats:
                    print "in inverse features mode"
                    #collapse extended feature space
                    #discard negative features in first half of the weight vector and negative features in the second half of the weight vector
                    pos_coef = predictor.coef_[0][0:(len(predictor.coef_[0]))/2]
                    pos_coef[pos_coef < 0] = 0
                    neg_coef = predictor.coef_[0][(len(predictor.coef_[0]))/2 : len(predictor.coef_[0])]
                    neg_coef[neg_coef > 0] = 0
                    rel_weights = pos_coef + neg_coef
                else:
                    rel_weights = predictor.coef_
                predictors.append(predictor)
                models[[np.array(sample_feats) - 1], i * no_classifier + l] = rel_weights 
        feats = []
        #determine the majority features 
        for i in range(models.shape[0]):
            if sum(models[i,:] > 0) >= math.ceil(k/2.0):
                feats.append(i)
        rownames = [baccs_s[i][3] for i in range(k)] 
        colnames = ['bacc', "pos_rec", "neg_rec"]
        baccs_s_np = np.array(baccs_s)[0:k,0:3].T
        baccs_s_np_p = ps.DataFrame(baccs_s_np).rename(dict((i,colnames[i]) for i in range(3)))
        ps.DataFrame(baccs_s_np_p).to_csv("%s/%s_perf.txt"%(self.model_out,pt_out), sep="\t", float_format = '%.3f',  header=rownames)
        #write all the features with their weights to disk
        rownames_extd = [str(baccs_s[i][3]) + "_" + str(l) for i in range(k) for l in range(no_classifier)] 
        models_df = ps.DataFrame(models)
        models_df.columns = rownames_extd
        for i in range(k):
            models_df[models_df.columns[i]] = models_df[models_df.columns[i]].map(lambda x: '%.3f' % x)
        models_df.to_csv("%s/%s_feats.txt"%(self.model_out,pt_out), sep="\t")
        #write majority features with their weights to disk
        #print feats
        id2pf = ps.DataFrame(self.get_pfam_names_and_descs(feats))
        print id2pf
        feat_df = ps.concat([id2pf.loc[:, feats], models_df.T.loc[:, feats]], axis = 0).T
        feat_df.columns = ["Pfam_acc", "Pfam_desc"] + rownames_extd
        columns_out = ["Pfam_acc"] + rownames_extd + ["Pfam_desc"]
        feat_df.to_csv("%s/%s_majority_features+weights.txt"%(self.model_out,pt_out), columns = columns_out, float_format='%.3f',  sep = "\t")
        id2pf.index = ("Pfam_acc", "Pfam_desc")
        id2pf.loc[:, feats].T.to_csv("%s/%s_majority_features.txt"%(self.model_out,pt_out), float_format='%.3f',  sep = "\t")
        #write coefficient matrix to disk
        #put column names
        #pickle the predictors
        dump(predictors, '%s/pickled/%s_predictors.pkl'%(self.model_out,pt_out))
    