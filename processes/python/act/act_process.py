# -*- coding: utf-8 -*-
"""
Kwiver process based on evaluating act

@author Ameya Shringi
"""

# Kwiver/Sprokit imports
from sprokit.pipeline import process
from kwiver.kwiver_process import KwiverProcess
from vital.types import ObjectTrackSet, Track, ObjectTrackState, DetectedObjectType,\
                        BoundingBox, DetectedObject

# ACT Imports
from exp_config import experiment_config, expcfg_from_file
from ACT_utils import *

import numpy as np
import caffe
import os 
import cv2
import time

class ACTProcess(KwiverProcess):
    """
    This process gets an image as input, spatially and temporal localizes the activity 
    and creates a detected object set out of it
    """

    def __init__(self, conf):
        KwiverProcess.__init__(self, conf)
        self.add_config_trait("exp", "exp",
                            '.', 'experiment configuration for ACT')
        self.declare_config_using_trait('exp')
        self.add_config_trait("model_itr", "model_itr",
                            "60000", "Iteration for the trained model") 
        self.declare_config_using_trait("model_itr")
        self.add_config_trait('img_width', 'img_width',
                            '1920', 'width of the original image')
        self.declare_config_using_trait('img_width')
        self.add_config_trait('img_height', 'img_height',
                            '1080', 'height of the original image')
        self.declare_config_using_trait('img_height')

        expcfg_from_file(self.config_value("exp"))

        # set up required flags
        required = process.PortFlags()
        required.add(self.flag_required)
        
        #  declare our ports ( port-name, flags)
        self.add_port_trait("rgb_image", "image", "rgb image for ACT")
        self.add_port_trait("flow_image", "image", "flow image for ACT")
        self.declare_input_port_using_trait('rgb_image', required )
        self.declare_input_port_using_trait('flow_image', required )
        self.declare_input_port_using_trait('timestamp', required )
        self.declare_output_port_using_trait('object_track_set', process.PortFlags() )

    def _reset_image_buffers(self):
        self.rgb_video = np.zeros([experiment_config.data.num_frames,
                                            3, experiment_config.train.imgsize,
                                            experiment_config.train.imgsize]) 
        self.flow_video = np.zeros([experiment_config.data.num_frames,
                                            3*experiment_config.test.number_flow,
                                            experiment_config.train.imgsize,
                                            experiment_config.train.imgsize])
        self.flow_buffer = np.zeros([experiment_config.test.number_flow, 
                                    experiment_config.train.imgsize, 
                                    experiment_config.train.imgsize, 3])
        


    def _configure(self):
        caffe.set_mode_gpu()
        caffe.set_device(experiment_config.gpu)
        model_dir = experiment_config.train.model_dir
        # Caffe model for ACT
        rgb_model = os.path.join(model_dir, "virat_RGB_iter_" + \
                str(self.config_value("model_itr")) + ".caffemodel")
        flow_model = os.path.join(model_dir, "virat_FLOW5_iter_" + \
                str(self.config_value("model_itr")) + ".caffemodel")
    
        if not os.path.exists(rgb_model):
            raise OSError( "rgb model path " + rgb_model + " not found" ) 
        if not os.path.exists(flow_model):
            raise OSError( "flow model path " + flow_model + " not found" ) 

        # Deploy prototxt 
        rgb_proto = os.path.join(model_dir, "online_deploy_RGB.prototxt")
        flow_proto = os.path.join(model_dir, "online_deploy_FLOW5.prototxt")
        
        # flow and rgb networks
        self.net_rgb = caffe.Net(rgb_proto, caffe.TEST, weights=rgb_model) 

        self.net_flow = caffe.Net(flow_proto, caffe.TEST, weights=flow_model)
        # flow buffer to store 5 frames
        self.flow_buffer = np.zeros([experiment_config.test.number_flow, 
                                    experiment_config.train.imgsize, 
                                    experiment_config.train.imgsize, 3])

        self.resolution_array = np.ones([experiment_config.data.num_frames*4])
        self.resolution_array[0::2] = self.resolution_array[0::2] * \
                                            int(self.config_value("img_width"))
        self.resolution_array[1::2] = self.resolution_array[1::2] * \
                                            int(self.config_value("img_height"))
        self._reset_image_buffers()
        self.rgb_kwargs = {}
        self.flow_kwargs = {}

    def create_track_set(self, all_detections, last_frame_id):
        tracks = []
        for detections in all_detections:
            all_bounding_boxes = detections[:experiment_config.data.num_frames*4]
            all_classes = detections[experiment_config.data.num_frames*4:]
            for class_index, class_score in enumerate(all_classes):
                # ignore background
                if class_index == 0:
                    continue
                detected_obj_type = DetectedObjectType(str(class_index), class_score)
                obj_track = Track()
                for box_index in range(0, all_bounding_boxes.shape[0], 4):
                    vital_bbox = BoundingBox(all_bounding_boxes[box_index], 
                                            all_bounding_boxes[box_index + 1],
                                            all_bounding_boxes[box_index + 2],
                                            all_bounding_boxes[box_index + 3])
                    detected_obj = DetectedObject(vital_bbox, class_score, \
                                                    detected_obj_type)
                    frame_id = last_frame_id - experiment_config.data.num_frames + \
                                box_index/4
                    obj_track_state = ObjectTrackState(frame_id, frame_id, \
                                                        detected_obj)
                    obj_track.append(obj_track_state)
                tracks.append(obj_track)
        return ObjectTrackSet(tracks)

    def _step(self):
        inp_rgb_img = self.grab_input_using_trait("rgb_image")
        inp_ts = self.grab_input_using_trait("timestamp")
        inp_flow_img = self.grab_input_using_trait("flow_image")

        start = time.time()
        # New video is starting
        if inp_ts.get_frame() == 1:
            self._reset_image_buffers()

        if inp_ts.get_frame()  <= experiment_config.test.number_flow:
            self.flow_buffer[inp_ts.get_frame()-1] = inp_flow_img.image().asarray()
        else:
            self.flow_buffer[:experiment_config.test.number_flow-1] = \
                    self.flow_buffer[1:experiment_config.test.number_flow]
            self.flow_buffer[experiment_config.test.number_flow-1] = \
                    inp_flow_img.image().asarray()
        rgb_image = cv2.resize(inp_rgb_img.image().asarray().astype(np.uint8), 
                                (experiment_config.train.imgsize, 
                                experiment_config.train.imgsize))
        
        flow_image = np.concatenate(self.flow_buffer, axis=2)
        # Bring image and flow channel to the front
        rgb_image = np.transpose(rgb_image, (2, 0, 1))
        flow_image = np.transpose(flow_image, (2, 0, 1))

        caffe.set_mode_gpu()
        caffe.set_device(experiment_config.gpu)
         
        buffer_index = (inp_ts.get_frame()-1)%experiment_config.data.num_frames

        # input data dimension is 1x3x300x300 and 1x15x300x300 for rgb and optical flow
        self.rgb_kwargs['data_stream' + str(buffer_index)] = \
                                                       rgb_image[np.newaxis, :] 
        self.flow_kwargs['data_stream' + str(buffer_index) + 'flow'] = \
                                                        flow_image[np.newaxis, :]
        if inp_ts.get_frame()%experiment_config.data.num_frames == 0:
            # forward of rgb with confidence and regression
            self.net_rgb.forward(end="mbox_conf_flatten", **self.rgb_kwargs)  
            # forward of flow5 with confidence and regression
            self.net_flow.forward(end="mbox_conf_flatten", **self.flow_kwargs)

            # Combine scores
            scores = 0.5 * (self.net_rgb.blobs['mbox_conf_flatten'].data + \
                    self.net_flow.blobs['mbox_conf_flatten'].data)

            self.net_rgb.blobs['mbox_conf_flatten'].data[...] = scores
            self.net_flow.blobs['mbox_conf_flatten'].data[...] = scores
            self.net_flow.blobs['mbox_loc'].data[...] = self.net_rgb.blobs['mbox_loc'].data
            
            # two forward passes, only for the last layer 
            # dets is the detections after per-class NMS and thresholding (stardard)
            # dets_all contains all the scores and regressions for all tubelets 
            dets = self.net_rgb.forward(start='detection_out')['detection_out'][:, 0, :, 1:]
            dets_all = self.net_flow.forward(start='detection_out_full')['detection_out_full'][:, 0, :, 1:]
            dets[:, :, 2:] *= self.resolution_array
            w, h = int(self.config_value("img_width")), \
                    int(self.config_value("img_height"))
            dets[:, :, 2::2] = np.maximum(0, np.minimum(w, dets[:, :, 2::2]))
            dets[:, :, 3::2] = np.maximum(0, np.minimum(h, dets[:, :, 3::2]))
            dets_all[:, :, 0:4*experiment_config.data.num_frames] *= self.resolution_array 
            dets_all[:, :, 0:4*experiment_config.data.num_frames:2] = \
                    np.maximum(0, np.minimum(w, 
                        dets_all[:, :, 0:4*experiment_config.data.num_frames:2]))
            dets_all[:,:, 1:4*experiment_config.data.num_frames:2] = \
                    np.maximum(0, np.minimum(h, dets_all[:, \
                                :, 1:4*experiment_config.data.num_frames:2]))
            idx = nms_tubelets(np.concatenate(
                        (dets_all[0, :, :5*experiment_config.data.num_frames], 
                        np.max(dets_all[0, :, 4*experiment_config.data.num_frames+1:], 
                                axis=1)[:, None]), axis=1), 0.7, 300)
            dets_all = dets_all[0, idx, :]
            obj_track_set = self.create_track_set(dets_all, 
                                                    inp_ts.get_frame())
            self.push_to_port_using_trait('object_track_set', obj_track_set)
        else:
            self.push_to_port_using_trait('object_track_set', ObjectTrackSet())
        end = time.time()
        print "Time to complete: " + str(end-start)


# ==================================================================
def __sprokit_register__():
    from sprokit.pipeline import process_factory

    module_name = 'python:kwiver.ACTProcess'

    if process_factory.is_process_module_loaded(module_name):
        return

    process_factory.add_process('ACTProcess', 'Apply ACT detector to image stream', ACTProcess)

    process_factory.mark_process_module_as_loaded(module_name)
    
