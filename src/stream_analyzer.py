import numpy as np
import time, math, scipy
from collections import deque
from scipy.signal import savgol_filter
from matplotlib import pyplot as plt
import matplotlib as mpl

from src import opc

from src.fft import getFFT
from src.utils import *


def lin2log_Setup(val_len,start,end,n,fs):
    fs = fs/2
    start = int(np.round(val_len/fs*start,0))
    if start <= 0:
        start = 1
    end = int(np.round(val_len/fs*end,0))
    
    #creates a vector the "desired" logarithmic scaled frequencies from start-frequency to end-frequency
    despos = np.linspace(np.log10(start),np.log10(end),n)
    despos = np.power(10,despos)
    despos = np.insert(despos,0,0)   
    despos = np.round(despos)
    despos = despos.astype("int")
    
    resultvec = np.zeros((len(despos)-1))
    print("Vectorindices: ", despos)      
    #to be filled result vector
    return resultvec,despos
    
def lin2log_Result(val,max,result,despos):
    for i in range(0,len(despos)-1):
        if despos[i] == despos[i+1]:	#in case resolution (specialy in the low frequencies) is to low and twice same position appears
            result[i] = result[i-1]
        else:
            result[i] = np.mean(val[despos[i]:despos[i+1]:1])
    return result


def matrixerize(input,mode,colour_M,client):
    start = time.time()
    x = colour_M.shape[0]
    y =colour_M.shape[1]
    #map input from min-max to 0-y
    minVal = np.amin(input)
    result = np.subtract(input,minVal)
    maxVal = np.amax(result)
    result = np.asarray(result / maxVal)
    result = np.multiply(result,y)
    result = np.round(result).astype(int)
    #create Matrix with x*y where frequency energies shown as bar height of "1"
    Ones_M = np.zeros((x,y))
    for i in range(0,x):
        Ones_M[i,:result[i]] = 1
    if mode==0:
        #gives that as output
        out = Ones_M.astype(int)
        print(out)
    elif mode ==1:
        #use that for pickung "colourpixels" from colourmatrix
        out = np.copy(colour_M)
        out[np.where(Ones_M == 0)] = [0,0,0]
        for i in range(0,x,2):
            out[i,:] = np.flip(out[i,:],0)
        #out = np.reshape(out,(x*y,3))
        out = list(map(tuple, out.reshape((x*y, 3))))
        #print(out)
        client.put_pixels(out)
    #print("Matrixerizezeit: ",time.time()-start)
    return out
    
    
    
def Colourmatrix(x,y):
    image = np.zeros((x,y,3), dtype=np.uint8)
    image = mpl.colors.rgb_to_hsv(image)
    for i in range(x):
        image[i,:] = [i/(x)*280/360,1,1]
    image = mpl.colors.hsv_to_rgb(image)*255
    image = image.astype(int)
    print("image=======:" ,image)
    return image

class Stream_Analyzer:
    """
    The Audio_Analyzer class provides access to continuously recorded
    (and mathematically processed) audio data.

    Arguments:

        device: int or None:      Select which audio stream to read .
        rate: float or None:      Sample rate to use. Defaults to something supported.
        FFT_window_size_ms: int:  Time window size (in ms) to use for the FFT transform
        updatesPerSecond: int:    How often to record new data.

    """

    def __init__(self,
        device = None,
        rate   = None,
        FFT_window_size_ms  = 50,
        updates_per_second  = 1000,
        smoothing_length_ms = 60,
        n_frequency_bins    = 51,
        visualize = True,
        verbose   = False,
        height    = 450,
        window_ratio = 24/9):

        self.n_frequency_bins = n_frequency_bins
        self.rate = rate
        self.verbose = verbose
        self.visualize = visualize
        self.height = height
        self.window_ratio = window_ratio
        #===================================FÜR BASTI :) ================================
        client = opc.Client('digiwall:7890')
        minFreq_out = 50
        maxFreq_out = 10000
        x_out = 8
        y_out = 8
        output_Mode = 1
        #===================================NICHT MEHR FÜR BASTI :'( ==============
        self.maxFreq_out = maxFreq_out
        self.x_out=x_out
        self.y_out=y_out
        self.output_Mode = output_Mode
        self.client = client

        try:
            from src.stream_reader_pyaudio import Stream_Reader
            self.stream_reader = Stream_Reader(
                device  = device,
                rate    = rate,
                updates_per_second  = updates_per_second,
                verbose = verbose)
        except:
            from src.stream_reader_sounddevice import Stream_Reader
            self.stream_reader = Stream_Reader(
                device  = device,
                rate    = rate,
                updates_per_second  = updates_per_second,
                verbose = verbose)

        self.rate = self.stream_reader.rate
        
        result_vec,despos = lin2log_Setup(n_frequency_bins,minFreq_out,maxFreq_out,x_out,self.rate)
        colour_M = Colourmatrix(self.x_out,self.y_out)
        
        self.result_vec = result_vec
        self.despos = despos
        self.colour_M = colour_M
        
        #Custom settings:
        self.rolling_stats_window_s    = 20     # The axis range of the FFT features will adapt dynamically using a window of N seconds
        self.equalizer_strength        = 0.20   # [0-1] --> gradually rescales all FFT features to have the same mean
        self.apply_frequency_smoothing = True   # Apply a postprocessing smoothing filter over the FFT outputs

        if self.apply_frequency_smoothing:
            self.filter_width = round_up_to_even(0.03*self.n_frequency_bins) - 1
        if self.visualize:
            from src.visualizer import Spectrum_Visualizer

        self.FFT_window_size = round_up_to_even(self.rate * FFT_window_size_ms / 1000)
        self.FFT_window_size_ms = 1000 * self.FFT_window_size / self.rate
        self.fft  = np.ones(int(self.FFT_window_size/2), dtype=float)
        self.fftx = np.arange(int(self.FFT_window_size/2), dtype=float) * self.rate / self.FFT_window_size

        self.data_windows_to_buffer = math.ceil(self.FFT_window_size / self.stream_reader.update_window_n_frames)
        self.data_windows_to_buffer = max(1,self.data_windows_to_buffer)

        # Temporal smoothing:
        # Currently the buffer acts on the FFT_features (which are computed only occasionally eg 30 fps)
        # This is bad since the smoothing depends on how often the .get_audio_features() method is called...
        self.smoothing_length_ms = smoothing_length_ms
        if self.smoothing_length_ms > 0:
            print("smoothietime")
            self.smoothing_kernel = get_smoothing_filter(self.FFT_window_size_ms, self.smoothing_length_ms, verbose=1)
            self.feature_buffer = numpy_data_buffer(len(self.smoothing_kernel), len(self.fft), dtype = np.float32, data_dimensions = 2)

        #This can probably be done more elegantly...
        self.fftx_bin_indices = np.logspace(np.log2(len(self.fftx)), 0, len(self.fftx), endpoint=True, base=2, dtype=None) - 1
        self.fftx_bin_indices = np.round(((self.fftx_bin_indices - np.max(self.fftx_bin_indices))*-1) / (len(self.fftx) / self.n_frequency_bins),0).astype(int)
        self.fftx_bin_indices = np.minimum(np.arange(len(self.fftx_bin_indices)), self.fftx_bin_indices - np.min(self.fftx_bin_indices))
        self.frequency_bin_energies = np.zeros(self.n_frequency_bins)
        self.frequency_bin_centres  = np.zeros(self.n_frequency_bins)
        
        self.fftx_indices_per_bin = []
        self.bin_indices_length = np.zeros(self.n_frequency_bins)
        for bin_index in range(self.n_frequency_bins):
            bin_frequency_indices = np.asarray(np.where(self.fftx_bin_indices == bin_index)[0])
            self.bin_indices_length[bin_index] = len(bin_frequency_indices)
            self.fftx_indices_per_bin.append(bin_frequency_indices)
            fftx_frequencies_this_bin = self.fftx[bin_frequency_indices]
            self.frequency_bin_centres[bin_index] = np.mean(fftx_frequencies_this_bin)
         
        self.bin_indices_length = self.bin_indices_length.astype(int)   #===== vector with number of indices/bin
        print(self.bin_indices_length)
        
        self.min_index = np.where(self.bin_indices_length > 1)[0][0]    #=== first index where number of indeces/bin (in that list) is >1
        self.bin_indices_matrix = np.zeros((self.n_frequency_bins,self.bin_indices_length[-1]))
        
        for i in range(self.n_frequency_bins):
            val = np.asarray(np.where(self.fftx_bin_indices == i))[0]
            self.bin_indices_matrix[i,:len(val)] = val.astype(int)
            
        print(self.bin_indices_matrix)
        self.bin_indices_matrix = self.bin_indices_matrix.astype(int)
        self.bin_indices_matrix_cropped = self.bin_indices_matrix[self.min_index:,:]
        print(self.bin_indices_matrix)
        print(self.bin_indices_matrix_cropped)
        print(self.min_index)
        #Hardcoded parameters:
        self.fft_fps = 30
        self.log_features = False   # Plot log(FFT features) instead of FFT features --> usually pretty bad
        self.delays = deque(maxlen=20)
        self.num_ffts = 0
        self.strongest_frequency = 0

        #Assume the incoming sound follows a pink noise spectrum:
        self.power_normalization_coefficients = np.logspace(np.log2(1), np.log2(np.log2(self.rate/2)), len(self.fftx), endpoint=True, base=2, dtype=None)
        self.rolling_stats_window_n = self.rolling_stats_window_s * self.fft_fps #Assumes ~30 FFT features per second
        self.rolling_bin_values = numpy_data_buffer(self.rolling_stats_window_n, self.n_frequency_bins, start_value = 25000)
        self.bin_mean_values = np.ones(self.n_frequency_bins)

        print("Using FFT_window_size length of %d for FFT ---> window_size = %dms" %(self.FFT_window_size, self.FFT_window_size_ms))
        print("##################################################################################################")

        #Let's get started:
        self.stream_reader.stream_start(self.data_windows_to_buffer)

        if self.visualize:
            print("visualiiiize")
            self.visualizer = Spectrum_Visualizer(self)
            self.visualizer.start()

    def update_rolling_stats(self):
        self.rolling_bin_values.append_data(self.frequency_bin_energies)
        self.bin_mean_values  = np.mean(self.rolling_bin_values.get_buffer_data(), axis=0)
        self.bin_mean_values  = np.maximum((1-self.equalizer_strength)*np.mean(self.bin_mean_values), self.bin_mean_values)

    def update_features(self, n_bins = 3):

        latest_data_window = self.stream_reader.data_buffer.get_most_recent(self.FFT_window_size)

        self.fft = getFFT(latest_data_window, self.rate, self.FFT_window_size, log_scale = self.log_features)
        #Equalize pink noise spectrum falloff:
        self.fft = self.fft * self.power_normalization_coefficients
        self.num_ffts += 1
        self.fft_fps  = self.num_ffts / (time.time() - self.stream_reader.stream_start_time)

        if self.smoothing_length_ms > 0:
            self.feature_buffer.append_data(self.fft)
            buffered_features = self.feature_buffer.get_most_recent(len(self.smoothing_kernel))
            if len(buffered_features) == len(self.smoothing_kernel):
                buffered_features = self.smoothing_kernel * buffered_features
                self.fft = np.mean(buffered_features, axis=0)

        self.strongest_frequency = self.fftx[np.argmax(self.fft)]

        #ToDo: replace this for-loop with pure numpy code
        # start1 = time.time()
        # for bin_index in range(self.n_frequency_bins):
            # self.frequency_bin_energies[bin_index] = np.mean(self.fft[self.fftx_indices_per_bin[bin_index]])
        # print("Zeit1: ",time.time()-start1)
        #print(self.frequency_bin_energies)

        
        # self.frequency_bin_energies2[:self.min_index] = self.fft[:self.min_index]
        # for bin_index in range(self.min_index,self.n_frequency_bins):
            # self.frequency_bin_energies2[bin_index] = np.mean(self.fft[self.fftx_indices_per_bin[bin_index]])
        # print(self.frequency_bin_energies2)
        # print(self.frequency_bin_energies2 == self.frequency_bin_energies)


        self.frequency_bin_energies[:self.min_index] = self.fft[:self.min_index]
        member = self.fft[0]
        self.fft[0]=np.NaN
        self.frequency_bin_energies[self.min_index:] = np.transpose(np.nanmean(np.transpose(self.fft[self.bin_indices_matrix_cropped]),axis=0))
        self.frequency_bin_energies[0] = member
        #print(self.frequency_bin_energies)
        # print(self.frequency_bin_energies3)
        # print(self.frequency_bin_energies3 - self.frequency_bin_energies)      
        #Beat detection ToDo:
        #https://www.parallelcube.com/2018/03/30/beat-detection-algorithm/
        #https://github.com/shunfu/python-beat-detector
        #https://pypi.org/project/vamp/

        return
    

    
    def get_audio_features(self):

        if self.stream_reader.new_data:  #Check if the stream_reader has new audio data we need to process
            if self.verbose:   
                start = time.time()
            self.update_features()
            self.update_rolling_stats()
            self.stream_reader.new_data = False
            self.frequency_bin_energies = np.nan_to_num(self.frequency_bin_energies, copy=True)
            if self.apply_frequency_smoothing:
                if self.filter_width > 3:
                    self.frequency_bin_energies = savgol_filter(self.frequency_bin_energies, self.filter_width, 3)
            self.frequency_bin_energies[self.frequency_bin_energies < 0] = 0
            
            out = lin2log_Result(self.frequency_bin_energies,self.maxFreq_out,self.result_vec,self.despos)
            start = time.time()
            #lin2log_Result(val,max,result,despos)
            out= matrixerize(out,self.output_Mode,self.colour_M,self.client)
            #print(time.time()-start)
            #matrixerize(input,x,y,mode)
            if self.verbose:
                self.delays.append(time.time() - start)
                avg_fft_delay = 1000.*np.mean(np.array(self.delays))
                avg_data_capture_delay = 1000.*np.mean(np.array(self.stream_reader.data_capture_delays))
                data_fps = self.stream_reader.num_data_captures / (time.time() - self.stream_reader.stream_start_time)
                print("\nAvg fft  delay: %.2fms  -- avg data delay: %.2fms" %(avg_fft_delay, avg_data_capture_delay))
                print("Num data captures: %d (%.2ffps)-- num fft computations: %d (%.2ffps)"
                    %(self.stream_reader.num_data_captures, data_fps, self.num_ffts, self.fft_fps))
            if self.visualize and self.visualizer._is_running:
                self.visualizer.update()
        return self.fftx, self.fft, self.frequency_bin_centres, self.frequency_bin_energies
