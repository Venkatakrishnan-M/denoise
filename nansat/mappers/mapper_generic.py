# Name:         mapper_generic.py
# Purpose:      Generic Mapper for L3/L4 satellite or modeling data
# Authors:      Asuka Yamakava, Anton Korosov, Morten Wergeland Hansen,
#               Aleksander Vines
# Copyright:    (c) NERSC
# Licence:      This file is part of NANSAT. You can redistribute it or modify
#               under the terms of GNU General Public License, v.3
#               http://www.gnu.org/licenses/gpl-3.0.html
import os
from dateutil.parser import parse
import datetime

import numpy as np
from scipy.io.netcdf import netcdf_file

try:
    from cfunits import Units
except:
    cfunitsInstalled = False
else:
    cfunitsInstalled = True

from nansat.nsr import NSR
from nansat.vrt import VRT, GeolocationArray
from nansat.tools import gdal, WrongMapperError


class Mapper(VRT):
    def __init__(self, inputFileName, gdalDataset, gdalMetadata, logLevel=30,
                 rmMetadatas=['NETCDF_VARNAME', '_Unsigned',
                              'ScaleRatio', 'ScaleOffset', 'dods_variable'],
                 **kwargs):
        # Remove 'NC_GLOBAL#' and 'GDAL_' and 'NANSAT_'
        # from keys in gdalDataset
        tmpGdalMetadata = {}
        geoMetadata = {}
        origin_is_nansat = False
        if not gdalMetadata:
            raise WrongMapperError
        for key in gdalMetadata.keys():
            newKey = key.replace('NC_GLOBAL#', '').replace('GDAL_', '')
            if 'NANSAT_' in newKey:
                geoMetadata[newKey.replace('NANSAT_', '')] = gdalMetadata[key]
                origin_is_nansat = True
            else:
                tmpGdalMetadata[newKey] = gdalMetadata[key]
        gdalMetadata = tmpGdalMetadata
        fileExt = os.path.splitext(inputFileName)[1]

        # Get file names from dataset or subdataset
        subDatasets = gdalDataset.GetSubDatasets()
        if len(subDatasets) == 0:
            fileNames = [inputFileName]
        else:
            fileNames = [f[0] for f in subDatasets]

        # add bands with metadata and corresponding values to the empty VRT
        metaDict = []
        xDatasetSource = ''
        yDatasetSource = ''
        firstXSize = 0
        firstYSize = 0
        for _, fileName in enumerate(fileNames):
            subDataset = gdal.Open(fileName)
            # choose the first dataset whith grid
            if (firstXSize == 0 and firstYSize == 0 and
                    subDataset.RasterXSize > 1 and subDataset.RasterYSize > 1):
                firstXSize = subDataset.RasterXSize
                firstYSize = subDataset.RasterYSize
                firstSubDataset = subDataset
                # get projection from the first subDataset
                projection = firstSubDataset.GetProjection()

            # take bands whose sizes are same as the first band.
            if (subDataset.RasterXSize == firstXSize and
                    subDataset.RasterYSize == firstYSize):
                if projection == '':
                    projection = subDataset.GetProjection()
                if ('GEOLOCATION_X_DATASET' in fileName or
                        'longitude' in fileName):
                    xDatasetSource = fileName
                elif ('GEOLOCATION_Y_DATASET' in fileName or
                        'latitude' in fileName):
                    yDatasetSource = fileName
                else:
                    for iBand in range(subDataset.RasterCount):
                        subBand = subDataset.GetRasterBand(iBand+1)
                        bandMetadata = subBand.GetMetadata_Dict()
                        if 'PixelFunctionType' in bandMetadata:
                            bandMetadata.pop('PixelFunctionType')
                        sourceBands = iBand + 1
                        # sourceBands = i*subDataset.RasterCount + iBand + 1

                        # generate src metadata
                        src = {'SourceFilename': fileName,
                               'SourceBand': sourceBands}
                        # set scale ratio and scale offset
                        scaleRatio = bandMetadata.get(
                            'ScaleRatio',
                            bandMetadata.get(
                                'scale',
                                bandMetadata.get('scale_factor', '')))
                        if len(scaleRatio) > 0:
                            src['ScaleRatio'] = scaleRatio
                        scaleOffset = bandMetadata.get(
                            'ScaleOffset',
                            bandMetadata.get(
                                'offset',
                                bandMetadata.get(
                                    'add_offset', '')))
                        if len(scaleOffset) > 0:
                            src['ScaleOffset'] = scaleOffset
                        # sate DataType
                        src['DataType'] = subBand.DataType

                        # generate dst metadata
                        # get all metadata from input band
                        dst = bandMetadata
                        # set wkv and bandname
                        dst['wkv'] = bandMetadata.get('standard_name', '')
                        # first, try the name metadata
                        if 'name' in bandMetadata:
                            bandName = bandMetadata['name']
                        else:
                            # if it doesn't exist get name from NETCDF_VARNAME
                            bandName = bandMetadata.get('NETCDF_VARNAME', '')
                            if len(bandName) == 0:
                                bandName = bandMetadata.get(
                                            'dods_variable', ''
                                            )

                            # remove digits added by gdal in
                            # exporting to netcdf...
                            if (len(bandName) > 0 and origin_is_nansat and
                                    fileExt == '.nc'):
                                if bandName[-1:].isdigit():
                                    bandName = bandName[:-1]
                                if bandName[-1:].isdigit():
                                    bandName = bandName[:-1]

                        # if still no bandname, create one
                        if len(bandName) == 0:
                            bandName = 'band_%03d' % iBand

                        dst['name'] = bandName

                        # remove non-necessary metadata from dst
                        for rmMetadata in rmMetadatas:
                            if rmMetadata in dst:
                                dst.pop(rmMetadata)

                        # append band with src and dst dictionaries
                        metaDict.append({'src': src, 'dst': dst})

        # create empty VRT dataset with geolocation only
        VRT.__init__(self, firstSubDataset, srcMetadata=gdalMetadata)

        # add bands with metadata and corresponding values to the empty VRT
        self._create_bands(metaDict)

        # Create complex data bands from 'xxx_real' and 'xxx_imag' bands
        # using pixelfunctions
        rmBands = []
        for iBandNo in range(self.dataset.RasterCount):
            iBand = self.dataset.GetRasterBand(iBandNo + 1)
            iBandName = iBand.GetMetadataItem('name')
            # find real data band
            if iBandName.find("_real") != -1:
                realBandNo = iBandNo
                realBand = self.dataset.GetRasterBand(realBandNo + 1)
                realDtype = realBand.GetMetadataItem('DataType')
                bandName = iBandName.replace(iBandName.split('_')[-1],
                                             '')[0:-1]
                for jBandNo in range(self.dataset.RasterCount):
                    jBand = self.dataset.GetRasterBand(jBandNo + 1)
                    jBandName = jBand.GetMetadataItem('name')
                    # find an imaginary data band corresponding to the real
                    # data band and create complex data band from the bands
                    if jBandName.find(bandName+'_imag') != -1:
                        imagBandNo = jBandNo
                        imagBand = self.dataset.GetRasterBand(imagBandNo + 1)
                        imagDtype = imagBand.GetMetadataItem('DataType')
                        dst = imagBand.GetMetadata()
                        dst['name'] = bandName
                        dst['PixelFunctionType'] = 'ComplexData'
                        dst['dataType'] = 10
                        src = [{'SourceFilename': fileNames[realBandNo],
                                'SourceBand':  1,
                                'DataType': realDtype},
                               {'SourceFilename': fileNames[imagBandNo],
                                'SourceBand': 1,
                                'DataType': imagDtype}]
                        self._create_band(src, dst)
                        self.dataset.FlushCache()
                        rmBands.append(realBandNo + 1)
                        rmBands.append(imagBandNo + 1)

        # Delete real and imaginary bands
        if len(rmBands) != 0:
            self.delete_bands(rmBands)

        if len(projection) == 0:
            # projection was not set automatically
            # get projection from GCPProjection
            projection = geoMetadata.get('GCPProjection', '')
        if len(projection) == 0:
            # no projection was found in dataset or metadata:
            # generate WGS84 by default
            projection = NSR().wkt
        # fix problem with MET.NO files where a, b given in m and XC/YC in km
        if ('UNIT["kilometre"' in projection and
            ',SPHEROID["Spheroid",6378273,7.331926543631893e-12]' in
                projection):
            projection = projection.replace(
                ',SPHEROID["Spheroid",6378273,7.331926543631893e-12]',
                '')
        # set projection
        self.dataset.SetProjection(self.repare_projection(projection))

        # check if GCPs were added from input dataset
        gcps = firstSubDataset.GetGCPs()
        # if no GCPs in input dataset: try to add GCPs from metadata
        if not gcps:
            gcps = self.add_gcps_from_metadata(geoMetadata)
        # if yet no GCPs: try to add GCPs from variables
        if not gcps:
            gcps = self.add_gcps_from_variables(inputFileName)

        if gcps:
            # get GCP projection and repare
            projection = self.repare_projection(geoMetadata.
                                                get('GCPProjection', ''))
            # add GCPs to dataset
            self.dataset.SetGCPs(gcps, projection)
            self._remove_geotransform()

        # Find proper bands and insert GEOLOCATION ARRAY into dataset
        if len(xDatasetSource) > 0 and len(yDatasetSource) > 0:
            self.add_geolocationArray(GeolocationArray(xDatasetSource,
                                                       yDatasetSource))

        elif not gcps:
            # if no GCPs found and not GEOLOCATION ARRAY set:
            #   Set Nansat Geotransform if it is not set automatically
            geoTransform = self.dataset.GetGeoTransform()
            if len(geoTransform) == 0:
                geoTransformStr = geoMetadata.get('GeoTransform',
                                                  '(0|1|0|0|0|0|1)')
                geoTransform = eval(geoTransformStr.replace('|', ','))
                self.dataset.SetGeoTransform(geoTransform)

        subMetadata = firstSubDataset.GetMetadata()

        if 'start_time' in gdalMetadata:
            self._set_time(parse(gdalMetadata['start_time']))
        elif 'start_date' in gdalMetadata:
            self._set_time(parse(gdalMetadata['start_date']))
        elif 'time_coverage_start' in gdalMetadata:
            time_coverage_start = gdalMetadata['time_coverage_start'].strip()
            # To account for datasets on the format YYYY-MM-DDZ which is
            # invalid since it has no time, but a timezone
            try:
                start_datetime = parse(time_coverage_start)
            except ValueError:
                if (len(time_coverage_start) == 11 and
                        time_coverage_start.endswith('Z')):
                    time_coverage_start = time_coverage_start[:10]
                    start_datetime = parse(time_coverage_start)
            self._set_time(start_datetime)
        # try to read from time variable
        elif (cfunitsInstalled and
                 'time#standard_name' in subMetadata and
                 subMetadata['time#standard_name'] == 'time' and
                 'time#units' in subMetadata and
                 'time#calendar' in subMetadata):

            ncFile = netcdf_file(inputFileName, 'r')
            timeLength = ncFile.variables['time'].shape[0]
            timeValueStart = ncFile.variables['time'][0]
            timeValueEnd = ncFile.variables['time'][-1]
            ncFile.close()
            try:
                timeDeltaStart = Units.conform(timeValueStart,
                                  Units(subMetadata['time#units'],
                                        calendar=subMetadata['time#calendar']),
                                  Units('days since 1950-01-01'))
            except ValueError:
                self.logger.error('calendar units are wrong: %s' %
                                  subMetadata['time#calendar'])
            else:
                time_coverage_start = (datetime.datetime(1950,1,1) +
                                   datetime.timedelta(float(timeDeltaStart)))
                self._set_time(time_coverage_start)
                self.dataset.SetMetadataItem('time_coverage_start',
                                          time_coverage_start.isoformat())

                if timeLength > 1:
                    timeDeltaEnd = Units.conform(timeValueStart,
                                          Units(subMetadata['time#units'],
                                                calendar=subMetadata['time#calendar']),
                                          Units('days since 1950-01-01'))
                else:
                    timeDeltaEnd = timeDeltaStart + 1
                time_coverage_end = (datetime.datetime(1950,1,1) +
                                     datetime.timedelta(float(timeDeltaEnd)))

                self.dataset.SetMetadataItem('time_coverage_end',
                                          time_coverage_end.isoformat())
        if 'sensor' not in gdalMetadata:
            self.dataset.SetMetadataItem('sensor', 'unknown')
        if 'satellite' not in gdalMetadata:
            self.dataset.SetMetadataItem('satellite', 'unknown')
        if 'source_type' not in gdalMetadata:
            self.dataset.SetMetadataItem('source_type', 'unknown')
        if 'platform' not in gdalMetadata:
            self.dataset.SetMetadataItem('platform', 'unknown')
        if 'instrument' not in gdalMetadata:
            self.dataset.SetMetadataItem('instrument', 'unknown')

        self.logger.info('Use generic mapper - OK!')

    def repare_projection(self, projection):
        '''Replace odd symbols in projection string '|' => ','; '&' => '"' '''
        return projection.replace("|", ",").replace("&", '"')

    def add_gcps_from_metadata(self, geoMetadata):
        '''Get GCPs from strings in metadata and insert in dataset'''
        gcpNames = ['GCPPixel', 'GCPLine', 'GCPX', 'GCPY']
        gcpAllValues = []

        # for all gcp coordinates
        for i, gcpName in enumerate(gcpNames):
            # scan throught metadata and find how many lines with each GCP
            gcpLineCount = 0
            for metaDataItem in geoMetadata:
                if gcpName in metaDataItem:
                    gcpLineCount += 1
            # concat all lines
            gcpString = ''
            for n in range(0, gcpLineCount):
                gcpLineName = '%s_%03d' % (gcpName, n)
                gcpString += geoMetadata[gcpLineName]
            # convert strings to floats
            gcpString = gcpString.strip().replace(' ', '')
            gcpValues = []
            # append all gcps from string
            for x in gcpString.split('|'):
                if len(x) > 0:
                    gcpValues.append(float(x))
            # gcpValues = [float(x) for x in gcpString.strip().split('|')]
            gcpAllValues.append(gcpValues)

        # create list of GDAL GCPs
        gcps = []
        for i in range(0, len(gcpAllValues[0])):
            gcps.append(gdal.GCP(gcpAllValues[2][i], gcpAllValues[3][i], 0,
                                 gcpAllValues[0][i], gcpAllValues[1][i]))

        return gcps

    def add_gcps_from_variables(self, fileName):
        ''' Get GCPs from GCPPixel, GCPLine, GCPX, GCPY, GCPZ variables '''
        gcpVariables = ['GCPX', 'GCPY', 'GCPZ', 'GCPPixel', 'GCPLine', ]
        # open input netCDF file for reading GCPs
        try:
            ncFile = netcdf_file(fileName, 'r')
        except (TypeError, IOError) as e:
            self.logger.info('%s' % e)
            return None

        # check if all GCP variables exist in the file
        if not all([var in ncFile.variables for var in gcpVariables]):
            return None

        # get data from GCP variables into array
        varData = [ncFile.variables[var][:] for var in gcpVariables]
        varData = np.array(varData)

        # close input file
        ncFile.close()

        # create list of GDAL.GCPs
        gcps = [gdal.GCP(float(x),
                         float(y),
                         float(z),
                         float(pixel),
                         float(line)) for x, y, z, pixel, line in varData.T]

        return gcps
