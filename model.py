import torch
from collections import OrderedDict
import torch.nn as nn

################### Mobilenet ########################

def conv(in_channels, out_channels, kernel_size=3, padding=1, bn=True, dilation=1, stride=1, relu=True, bias=True):
    modules = [nn.Conv2d(in_channels, out_channels, kernel_size, stride, padding, dilation, bias=bias)]
    if bn:
        modules.append(nn.BatchNorm2d(out_channels))
    if relu:
        modules.append(nn.ReLU(inplace=True))
    return nn.Sequential(*modules)


def conv_dw(in_channels, out_channels, kernel_size=3, padding=1, stride=1, dilation=1):
    return nn.Sequential(
        nn.Conv2d(in_channels, in_channels, kernel_size, stride, padding, dilation=dilation, groups=in_channels, bias=False),
        nn.BatchNorm2d(in_channels),
        nn.ReLU(inplace=True),

        nn.Conv2d(in_channels, out_channels, 1, 1, 0, bias=False),
        nn.BatchNorm2d(out_channels),
        nn.ReLU(inplace=True),
    )


def conv_dw_no_bn(in_channels, out_channels, kernel_size=3, padding=1, stride=1, dilation=1):
    return nn.Sequential(
        nn.Conv2d(in_channels, in_channels, kernel_size, stride, padding, dilation=dilation, groups=in_channels, bias=False),
        nn.ELU(inplace=True),

        nn.Conv2d(in_channels, out_channels, 1, 1, 0, bias=False),
        nn.ELU(inplace=True),
    )

class Cpm(nn.Module):
    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.align = conv(in_channels, out_channels, kernel_size=1, padding=0, bn=False)
        self.trunk = nn.Sequential(
            conv_dw_no_bn(out_channels, out_channels),
            conv_dw_no_bn(out_channels, out_channels),
            conv_dw_no_bn(out_channels, out_channels)
        )
        self.conv = conv(out_channels, out_channels, bn=False)

    def forward(self, x):
        x = self.align(x)
        x = self.conv(x + self.trunk(x))
        torch.nn.init.xavier_uniform_(self.align)
        torch.nn.init.xavier_uniform_(self.trunk[0])
        torch.nn.init.xavier_uniform_(self.trunk[1])
        torch.nn.init.xavier_uniform_(self.trunk[2])
        torch.nn.init.xavier_uniform_(self.conv)
        return x


class InitialStage(nn.Module):
    def __init__(self, num_channels, num_heatmaps, num_pafs):
        super().__init__()
        self.trunk = nn.Sequential(
            conv(num_channels, num_channels, bn=False),
            conv(num_channels, num_channels, bn=False),
            conv(num_channels, num_channels, bn=False)
        )
        self.heatmaps = nn.Sequential(
            conv(num_channels, 512, kernel_size=1, padding=0, bn=False),
            conv(512, num_heatmaps, kernel_size=1, padding=0, bn=False, relu=False)
        )
        self.pafs = nn.Sequential(
            conv(num_channels, 512, kernel_size=1, padding=0, bn=False),
            conv(512, num_pafs, kernel_size=1, padding=0, bn=False, relu=False)
        )

    def forward(self, x):
        trunk_features = self.trunk(x)
        heatmaps = self.heatmaps(trunk_features)
        pafs = self.pafs(trunk_features)
        
        torch.nn.init.xavier_uniform_(self.trunk[0][0])
        torch.nn.init.xavier_uniform_(self.trunk[1][0])
        torch.nn.init.xavier_uniform_(self.trunk[2][0])
        torch.nn.init.xavier_uniform_(self.heatmaps[0][0])
        torch.nn.init.xavier_uniform_(self.heatmaps[1][0])
        torch.nn.init.xavier_uniform_(self.pafs[0][0])
        torch.nn.init.xavier_uniform_(self.pafs[1][0])
        
        
        return [heatmaps, pafs]


class RefinementStageBlock(nn.Module):
    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.initial = conv(in_channels, out_channels, kernel_size=1, padding=0, bn=False)
        self.trunk = nn.Sequential(
            conv(out_channels, out_channels),
            conv(out_channels, out_channels, dilation=2, padding=2)
        )

    def forward(self, x):
        initial_features = self.initial(x)
        trunk_features = self.trunk(initial_features)
        
        torch.nn.init.xavier_uniform_(self.initial)
        torch.nn.init.xavier_uniform_(self.trunk[0][0])
        torch.nn.init.xavier_uniform_(self.trunk[1][0])
        return initial_features + trunk_features


class RefinementStage(nn.Module):
    def __init__(self, in_channels, out_channels, num_heatmaps, num_pafs):
        super().__init__()
        self.trunk = nn.Sequential(
            RefinementStageBlock(in_channels, out_channels),
            RefinementStageBlock(out_channels, out_channels),
            RefinementStageBlock(out_channels, out_channels),
            RefinementStageBlock(out_channels, out_channels),
            RefinementStageBlock(out_channels, out_channels)
        )
        self.heatmaps = nn.Sequential(
            conv(out_channels, out_channels, kernel_size=1, padding=0, bn=False),
            conv(out_channels, num_heatmaps, kernel_size=1, padding=0, bn=False, relu=False)
        )
        self.pafs = nn.Sequential(
            conv(out_channels, out_channels, kernel_size=1, padding=0, bn=False),
            conv(out_channels, num_pafs, kernel_size=1, padding=0, bn=False, relu=False)
        )

    def forward(self, x):
        trunk_features = self.trunk(x)
        heatmaps = self.heatmaps(trunk_features)
        pafs = self.pafs(trunk_features)
        return [heatmaps, pafs]


class PoseEstimationWithMobileNet(nn.Module):
    def __init__(self, num_refinement_stages=1, num_channels=128, num_heatmaps=19, num_pafs=38):
        super().__init__()
        self.model = nn.Sequential(
            conv(     3,  32, stride=2, bias=False),
            conv_dw( 32,  64),
            conv_dw( 64, 128, stride=2),
            conv_dw(128, 128),
            conv_dw(128, 256, stride=2),
            conv_dw(256, 256),
            conv_dw(256, 512),  # conv4_2
            conv_dw(512, 512, dilation=2, padding=2),
            conv_dw(512, 512),
            conv_dw(512, 512),
            conv_dw(512, 512),
            conv_dw(512, 512)   # conv5_5
        )
        self.cpm = Cpm(512, num_channels)

        self.initial_stage = InitialStage(num_channels, num_heatmaps, num_pafs)
        self.refinement_stages = nn.ModuleList()
        for idx in range(num_refinement_stages):
            self.refinement_stages.append(RefinementStage(num_channels + num_heatmaps + num_pafs, num_channels,
                                                          num_heatmaps, num_pafs))

    def forward(self, x):
        backbone_features = self.model(x)
        backbone_features = self.cpm(backbone_features)

        stages_output = self.initial_stage(backbone_features)
        for refinement_stage in self.refinement_stages:
            stages_output.extend(
                refinement_stage(torch.cat([backbone_features, stages_output[-2], stages_output[-1]], dim=1)))
            

        torch.nn.init.xavier_uniform_(self.model[0][0])
        torch.nn.init.xavier_uniform_(self.model[1][0])
        torch.nn.init.xavier_uniform_(self.model[1][3])
        torch.nn.init.xavier_uniform_(self.model[2][0])
        torch.nn.init.xavier_uniform_(self.model[2][3])
        torch.nn.init.xavier_uniform_(self.model[3][0])
        torch.nn.init.xavier_uniform_(self.model[3][3])
        torch.nn.init.xavier_uniform_(self.model[4][0])
        torch.nn.init.xavier_uniform_(self.model[4][3])
        torch.nn.init.xavier_uniform_(self.model[5][0])
        torch.nn.init.xavier_uniform_(self.model[5][3])
        torch.nn.init.xavier_uniform_(self.model[6][0])
        torch.nn.init.xavier_uniform_(self.model[6][3])
        torch.nn.init.xavier_uniform_(self.model[7][0])
        torch.nn.init.xavier_uniform_(self.model[7][3])
        torch.nn.init.xavier_uniform_(self.model[8][0])
        torch.nn.init.xavier_uniform_(self.model[8][3])
        torch.nn.init.xavier_uniform_(self.model[9][0])
        torch.nn.init.xavier_uniform_(self.model[9][3])
        torch.nn.init.xavier_uniform_(self.model[10][0])
        torch.nn.init.xavier_uniform_(self.model[10][3])
        
        return stages_output

if __name__ == "__main__":

    import time 
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
#    device = torch.device("cpu")
    input = torch.Tensor(2, 3, 368, 368).to(device)

#     model_CMU = bodypose_model().to(device)
#     model_CMU.load_state_dict(torch.load('weights/bodypose_model'))
#     model_CMU.eval()
    
    model_Mobilenet = PoseEstimationWithMobileNet().to(device)
    model_Mobilenet.load_state_dict(torch.load('weights/MobileNet_bodypose_model'))
    model_Mobilenet.eval()
    
    since = time.time()
    
#     PAF_CMU, Heatmap_CMU = model_CMU(input)
#     print('CMU PAF shape and Heatmap shape', PAF_CMU.shape, Heatmap_CMU.shape)
    t1 = time.time()
#     print('CMU Inference time is {:2.3f} seconds'.format(t1 - since))
    
    stages_output= model_Mobilenet(input)
    PAF_Mobilenet, Heatmap_Mobilenet = stages_output[-1], stages_output[-2]
    print('Mobilenet PAF shape and Heatmap shape', PAF_Mobilenet.shape, Heatmap_Mobilenet.shape)
    print('Mobilenet Inference time is {:2.3f} seconds'.format(time.time() - t1))


