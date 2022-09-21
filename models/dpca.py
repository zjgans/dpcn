import torch.nn as nn
import torch
import torch.nn.functional as F
from torch.nn.modules.utils import _quadruple

class Self_Dynamic_Prototype(nn.Module):
    def __init__(self,proto_size,feature_dim,hidden_dim,tem_update,temp_gather, shrink_thres=0):
        super(Self_Dynamic_Prototype, self).__init__()
        self.proto_size = proto_size
        self.feature_dim = feature_dim
        self.hidden_dim = hidden_dim
        self.tem_update = tem_update
        self.tem_gather = temp_gather
        self.shrink_thres = shrink_thres
        # self.no_local_att = NonLocalSelfAttention(in_channels=feature_dim, inter_channels=hidden_dim)

        self.Multi_heads = nn.Linear(hidden_dim,proto_size,bias=False)
        self.proto_concept = nn.Sequential(nn.Conv2d(feature_dim,hidden_dim,kernel_size=1),
                                         nn.BatchNorm2d(hidden_dim),
                                         nn.ReLU())
        self.theta = nn.Conv2d(feature_dim,hidden_dim,kernel_size=1)
        self.phi = nn.Conv2d(feature_dim,hidden_dim,kernel_size=1)
        self.g = nn.Conv2d(feature_dim,hidden_dim,kernel_size=1)

        self.o = nn.Sequential(nn.Conv2d(hidden_dim, feature_dim, kernel_size=1),
                               nn.BatchNorm2d(feature_dim),
                               nn.ReLU())
        self.alpha = nn.Parameter(torch.zeros(1), requires_grad=True)

    def get_score(self,proto,query):
        bs,n,d = query.size()
        bs,m,d = proto.size()

        score = torch.bmm(query,proto.permute(0,2,1))
        score = score.view(bs,n,m)

        score_query = F.softmax(score,dim=1)
        score_proto = F.softmax(score,dim=2)

        return score_query,score_proto

    def forward(self,key,query):

        # non local attention
        batch_size,c,h,w = key.size()

        theta = self.theta(key)
        theta = theta.view(batch_size,self.hidden_dim,-1)
        phi = self.phi(key).view(batch_size,self.hidden_dim,-1)
        g = self.g(key).view(batch_size,self.hidden_dim,-1).permute(0,2,1)

        nlsa_atten = torch.matmul(theta.permute(0,2,1),phi)
        nlsa_atten = torch.softmax(nlsa_atten,dim=-1)
        nlsa_out = torch.matmul(nlsa_atten,g).permute(0,2,1).view(batch_size,self.hidden_dim,h,w)

        # ===================================================================================
        key_ = self.proto_concept(key)
        batch_size,dim,h,w = key_.size()
        key_ = key_.permute(0,2,3,1)
        query_ = key_.contiguous().view(batch_size,-1,dim)

        if self.training:

                multi_heads_weights = self.Multi_heads(key_)
                multi_heads_weights = multi_heads_weights.view(batch_size,h*w,self.proto_size,1)
                multi_heads_weights = F.softmax(multi_heads_weights,dim=1)

                key_ = key_.contiguous().view(batch_size,h*w,dim)
                protos = multi_heads_weights * key_.unsqueeze(-2)
                protos = protos.sum(1)

                # updated_query, fea_loss, cst_loss, dis_loss = self.query_loss(query,protos)
                updated_query, fea_loss, cst_loss, dis_loss =  self.query_loss(query_,protos)

                updated_query = updated_query.permute(0,2,1)
                updated_query = updated_query.contiguous().view(batch_size,dim,h,w)
                updated_query = updated_query + nlsa_out
                updated_query = self.o(updated_query) + query

                return updated_query, fea_loss, cst_loss, dis_loss
        else:

            multi_heads_weights = self.Multi_heads(key_)
            multi_heads_weights = multi_heads_weights.view(batch_size, h * w, self.proto_size, 1)
            multi_heads_weights = F.softmax(multi_heads_weights, dim=1)

            key_ = key_.contiguous().view(batch_size, h * w, dim)
            protos = multi_heads_weights * key_.unsqueeze(-2)
            protos = protos.sum(1)

            #updated_query, fea_loss, cst_loss, dis_loss = self.query_loss(query,protos)
            updated_query, fea_loss = self.query_loss(query_, protos)

            updated_query = updated_query.permute(0, 2, 1)
            updated_query = updated_query.contiguous().view(batch_size, dim, h, w)
            updated_query = updated_query + nlsa_out
            updated_query = self.o(updated_query) + query

            return updated_query

    def query_loss(self,query,protos):
        batch_size, n, dim = query.size()

        if self.training:
                protos_ = F.normalize(protos,dim=-1)
                dis = 1-distance(protos_.unsqueeze(1),protos_.unsqueeze(2))

                mask = dis>0
                dis = dis * mask.float()
                dis = torch.triu(dis,diagonal=1)
                dis_loss = dis.sum(1).sum(1)*2/(self.proto_size*(self.proto_size-1))
                dis_loss = dis_loss.mean()

                cst_loss = mean_distance(protos_[1:],protos_[:-1])
                loss_mse = torch.nn.MSELoss()
                protos = F.normalize(protos,dim=-1)

                softmax_score_query, softmax_score_proto = self.get_score(protos,query)
                new_query = softmax_score_proto.unsqueeze(-1)*protos.unsqueeze(1)
                new_query = new_query.sum(2)
                new_query = F.normalize(new_query,dim=-1)

                _,gathering_indices = torch.topk(softmax_score_proto,2,dim=-1)
                pos = torch.gather(protos,1,gathering_indices[:,:,:1].repeat(1,1,dim))
                fea_loss = loss_mse(query,pos)
                return new_query,fea_loss,cst_loss,dis_loss

        else:
            loss_mse = torch.nn.MSELoss(reduction='none')
            protos = F.normalize(protos,dim=-1)
            softmax_score_query, softmax_score_proto = self.get_score(protos, query)

            new_query = softmax_score_proto.unsqueeze(-1) * protos.unsqueeze(1)
            new_query = new_query.sum(2)
            new_query = F.normalize(new_query, dim=-1)

            _, gathering_indices = torch.topk(softmax_score_proto, 2, dim=-1)
            pos = torch.gather(protos,1, gathering_indices[:, :, :1].repeat(1, 1, dim))
            fea_loss = loss_mse(query, pos)

            return new_query,fea_loss
