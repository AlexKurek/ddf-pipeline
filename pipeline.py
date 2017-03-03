#!/usr/bin/env python

# Routine is to use killms/ddf to self-calibrate the data
import os,sys
import os.path
from auxcodes import report,run,find_imagenoise,warn,die
from options import options,print_options
from shutil import copyfile,rmtree
import pyrap.tables as pt
from modify_mask import modify_mask
from make_extended_mask import make_extended_mask,merge_mask,add_manual_mask
from histmsamp import find_uvmin,sumdico
import numpy as np
from astropy.io import fits

def logfilename(s,options=None):
    if options is None:
        options=o
    if options['logging'] is not None:
        return options['logging']+'/'+s 
    else:
        return None

def check_imaging_weight(mslist_name):

    report('Checking for IMAGING_WEIGHT in input MSS')
    mslist=[s.strip() for s in open(mslist_name).readlines()]
    for ms in mslist:
        t = pt.table(ms)
        try:
            dummy=t.getcoldesc('IMAGING_WEIGHT')
        except RuntimeError:
            dummy=None
        t.close()
        if dummy is not None:
            warn('Table '+ms+' already has imaging weights')
        else:
            pt.addImagingColumns(ms)

def ddf_image(imagename,mslist,cleanmask=None,cleanmode='HMP',ddsols=None,applysols=None,threshold=None,majorcycles=3,use_dicomodel=False,robust=0,beamsize=None,reuse_psf=False,reuse_dirty=False,verbose=False,saveimages=None,imsize=None,cellsize=None,uvrange=None,colname='CORRECTED_DATA',peakfactor=0.1,dicomodel_base=None,options=None,do_decorr=None,normalization=None,dirty_from_resid=False,clusterfile=None,HMPsize=None,automask=True,automask_threshold=10.0,smooth=False,noweights=False,cubemode=False):
    # saveimages lists _additional_ images to save
    if saveimages is None:
        saveimages=''
    saveimages+='onNeds'
    if options is None:
        options=o # attempt to get global if it exists

    if HMPsize is None:
        HMPsize=options['HMPsize']
    if do_decorr is None:
        do_decorr=options['do_decorr']
    if beamsize is None:
        beamsize=options['psf_arcsec']
    if imsize is None:
        imsize=options['imsize']
    if cellsize is None:
        cellsize=options['cellsize']

    cache_dir=options['cache_dir']
    if cache_dir is None:
        cache_dir='.'

    if majorcycles>0:
        fname=imagename+'.app.restored.fits'
    else:
        fname=imagename+'.dirty.fits'

    runcommand = "DDF.py --Output-Name=%s --Data-MS=%s --Deconv-PeakFactor %f --Data-ColName %s --Parallel-NCPU=%i --Image-Mode=Clean --Deconv-CycleFactor=0 --Deconv-MaxMinorIter=1000000 --Deconv-MaxMajorIter=%s --Deconv-Mode %s --Beam-Model=LOFAR --Beam-LOFARBeamMode=A --Weight-Robust %f --Weight-ColName IMAGING_WEIGHT --Image-NPix=%i --CF-wmax 50000 --CF-Nw 100 --Output-Also %s --Image-Cell %f --Facets-NFacets=11 --SSDClean-NEnlargeData 0 --Freq-NDegridBand 1 --Output-RestoringBeam %f --Beam-NBand 1 --Facets-DiamMax 1.5 --Facets-DiamMin 0.1 --Deconv-RMSFactor=3.0 --Data-Sort 1 --Cache-Dir=%s"%(imagename,mslist,peakfactor,colname,options['NCPU_DDF'],majorcycles,cleanmode,robust,imsize,saveimages,float(cellsize),beamsize,cache_dir)
    
    if cubemode:
        channels=len(open(mslist).readlines())
        runcommand+=' --Output-Cubes I --Freq-NBand=%i' % channels
    else:
        runcommand+=' --Freq-NBand=2'
    
    if do_decorr:
        runcommand += ' --RIME-DecorrMode=FT'

    if cleanmode == 'SSD':
        runcommand += ' --SSDClean-SSDSolvePars [S,Alpha] --SSDClean-BICFactor 0'
    if clusterfile is not None:
        runcommand += ' --Facets-CatNodes=%s' % clusterfile
    if automask:
        runcommand += ' --Mask-Auto=1 --Mask-SigTh=%.2f' % automask_threshold
    if cleanmask is not None:
        runcommand += ' --Mask-External=%s'%cleanmask
    if applysols is not None:
        if normalization is not None:
            runcommand += ' --DDESolutions-GlobalNorm=Mean'+normalization
        runcommand += ' --DDESolutions-DDModeGrid=%s --DDESolutions-DDModeDeGrid=%s --DDESolutions-DDSols=%s'%(applysols,applysols,ddsols)
    if use_dicomodel:
        if dicomodel_base is not None:
            runcommand += ' --Predict-InitDicoModel=%s.DicoModel' % dicomodel_base
        else:
            raise RuntimeError('use_dicomodel is set but no dicomodel supplied')
    if threshold is not None:
        runcommand += ' --Deconv-FluxThreshold=%f'%threshold
    if uvrange is not None:
        runcommand += ' --Selection-UVRangeKm=[%f,%f]' % (uvrange[0],uvrange[1])
    if dirty_from_resid and reuse_dirty:
        raise RuntimeError('Cannot combine reuse_dirty and dirty_from_resid')
    if dirty_from_resid:
        # possible that crashes could destroy the cache, so need to check
        if os.path.exists(mslist+'.ddfcache/LastResidual'):
            runcommand += ' --Cache-Dirty forceresidual'
    if reuse_dirty:
        if os.path.exists(mslist+'.ddfcache/Dirty'):
            runcommand += ' --Cache-Dirty forcedirty'
    if reuse_psf:
        if os.path.exists(mslist+'.ddfcache/PSF'):
            runcommand += ' --Cache-PSF force'

    if HMPsize is not None:
        runcommand += ' --SSDClean-MinSizeInitHMP=%i' % HMPsize

    if options['nobar']:
        runcommand += ' --Log-Boring=1'

    if smooth:
        runcommand += ' --Beam-Smooth=1'

    if noweights:
        runcommand += ' --Weight-ColName=None'

    if options['restart'] and os.path.isfile(fname):
        warn('File '+fname+' already exists, skipping DDF step')
        if verbose:
            print 'would have run',runcommand
    else:
         run(runcommand,dryrun=options['dryrun'],log=logfilename('DDF-'+imagename+'.log',options=options),quiet=options['quiet'])

def make_external_mask(fname,templatename,use_tgss=True,options=None,extended_use=None,clobber=False):
    if options is None:
        options=o # attempt to get global
    if o['restart'] and os.path.isfile(fname) and not clobber:
        warn('External mask already exists, not creating it')
    else:
        report('Make blank external mask')
        hdus=fits.open(templatename)
        hdus[0].data=np.zeros_like(hdus[0].data,dtype=np.int32)
        hdus.writeto(fname,clobber=True)
        hdus.close()
        if use_tgss and options['tgss'] is not None:
            report('Merging the mask with TGSS catalogue')
            # TGSS path is provided, this means we want to add the positions of bright TGSS sources to the mask
            modify_mask(fname,fname,options['tgss'],options['tgss_radius'],options['tgss_flux'],do_extended=options['tgss_extended'],cellsize=options['cellsize'],pointsize=o['tgss_pointlike'])

        if options['region'] is not None:
            report('Merging with mask with user-specified region')
            add_manual_mask(fname,options['region'],fname)

        if options['extended_size'] is not None and extended_use is not None:
            report('Merging with automatic extended mask')
            merge_mask(fname,extended_use,fname)

def make_mask(imagename,thresh,verbose=False,options=None,external_mask=None):
    # mask_use specifies a mask file to use
    if options is None:
        options=o # attempt to get global

    fname=imagename+'.mask.fits'
    runcommand = "MakeMask.py --RestoredIm=%s --Th=%s --Box=50,2"%(imagename,thresh)
    if options['restart'] and os.path.isfile(fname):
        warn('File '+fname+' already exists, skipping MakeMask step')
        if verbose:
            print 'Would have run',runcommand
    else:
        run(runcommand,dryrun=options['dryrun'],log=logfilename('MM-'+imagename+'.log',options=options),quiet=options['quiet'])
        if external_mask is not None:
            merge_mask(fname,'external_mask.fits',fname)

def killms_data(imagename,mslist,outsols,clusterfile=None,colname='CORRECTED_DATA',niterkf=6,dicomodel=None,uvrange=None):
    # run killms individually on each MS -- allows restart if it failed in the middle
    filenames=[l.strip() for l in open(mslist,'r').readlines()]
    for f in filenames:
        checkname=f+'/killMS.'+outsols+'.sols.npz'
        if o['restart'] and os.path.isfile(checkname):
            warn('Solutions file '+checkname+' already exists, not running killMS step')
        else:
            runcommand = "killMS.py --MSName %s --SolverType KAFCA --PolMode Scalar --BaseImageName %s --dt %i --Weighting Natural --BeamMode LOFAR --LOFARBeamMode=A --NIterKF %i --CovQ 0.1 --LambdaKF=%f --NCPU %i --OutSolsName %s --NChanSols %i --InCol %s"%(f,imagename,o['dt'],niterkf, o['LambdaKF'], o['NCPU_killms'], outsols, o['NChanSols'],colname)
            if uvrange is not None:
                runcommand+=' --UVMinMax=%f,%f' % (uvrange[0], uvrange[1])
            if clusterfile is not None:
                runcommand+=' --NodesFile '+clusterfile
            if dicomodel is not None:
                runcommand+=' --DicoModel '+dicomodel
            if o['nobar']:
                runcommand+=' --DoBar=0'
            rootfilename=outsols.split('/')[-1]
            run(runcommand,dryrun=o['dryrun'],log=logfilename('KillMS-'+f+'_'+rootfilename+'.log'),quiet=o['quiet'])

def make_model(maskname,imagename):
    # returns True if the step was run, False if skipped
    fname=imagename+'.npy'
    if o['restart'] and os.path.isfile(fname):
        warn('File '+fname+' already exists, skipping MakeModel step')
        return False
    else:
        runcommand = "MakeModel.py --MaskName=%s --BaseImageName=%s --NCluster=%i --DoPlot=0"%(maskname,imagename,o['ndir'])
        run(runcommand,dryrun=o['dryrun'],log=logfilename('MakeModel-'+maskname+'.log'),quiet=o['quiet'])
        return True

def mask_dicomodel(indico,maskname,outdico):
    if o['restart'] and os.path.isfile(outdico):
        warn('File '+outdico+' already exists, skipping MaskDicoModel step')
        return False
    else:
        runcommand = "MaskDicoModel.py --MaskName=%s --InDicoModel=%s --OutDicoModel=%s"%(maskname,indico,outdico) 
        run(runcommand,dryrun=o['dryrun'],log=logfilename('MaskDicoModel-'+maskname+'.log'),quiet=o['quiet'])
        return True

def clearcache(mslist,cachedir):
    report('Clearing cache for '+mslist)
    if cachedir is not None:
        prevdir=os.getcwd()
        os.chdir(cachedir)
    try:
        rmtree(mslist+'.ddfcache')
    except OSError:
        pass
    filenames=[l.strip() for l in open(mslist,'r').readlines()]
    for f in filenames:
        try:
            rmtree(f+'.ddfcache')
        except OSError:
            pass
    if cachedir is not None:
        os.chdir(prevdir)

def optimize_uvmin(rootname,mslist,colname):
    report('Optimizing uvmin for self-cal')
    level=sumdico(rootname)
    result=find_uvmin(mslist,level,colname=colname)*1.1
    print 'Will use shortest baseline of',result,'km'
    return result

if __name__=='__main__':
    # Main loop
    if len(sys.argv)<2:
        warn('pipeline.py must be called with a parameter file.\nSee below for a complete list of possible options with their default values.')
        print_options()
        sys.exit(1)

    o=options(sys.argv[1])
    uvrange=[o['image_uvmin'],1000]
    killms_uvrange=[0,1000]
    if o['solutions_uvmin'] is not None:
        killms_uvrange[0]=o['solutions_uvmin']
    if o['mslist'] is None:
        die('MS list must be specified')

    if o['logging'] is not None and not os.path.isdir(o['logging']):
        os.mkdir(o['logging'])

    # Set column name for first steps
    colname=o['colname']

    # Clear the shared memory
    run('CleanSHM.py',dryrun=o['dryrun'])    
    if o['clearcache']:
        # Clear the cache, we don't know where it's been
        clearcache(o['mslist'],o['cache_dir'])
        if o['full_mslist'] is not None:
            clearcache(o['full_mslist'],o['cache_dir'])

    # Check imaging weights -- needed before DDF
    check_imaging_weight(o['mslist'])

    # Image but don't clean to sort out WCS for mask. We use noweights
    # since new versions of the code can't cope with the zero weights
    # set by check_imaging_weights
    ddf_image('image_dirin_SSD_init',o['mslist'],cleanmask=None,cleanmode='SSD',majorcycles=0,robust=o['robust'],reuse_psf=False,reuse_dirty=False,peakfactor=0.05,colname=colname,clusterfile=None,noweights=True,uvrange=uvrange)
    external_mask='external_mask.fits'
    make_external_mask(external_mask,'image_dirin_SSD_init.dirty.fits',use_tgss=True,clobber=False)

    # Deep SSD clean with this external mask and automasking
    ddf_image('image_dirin_SSD',o['mslist'],cleanmask=external_mask,cleanmode='SSD',majorcycles=4,robust=o['robust'],reuse_psf=True,reuse_dirty=True,peakfactor=0.05,colname=colname,clusterfile=None,automask=True,automask_threshold=o['thresholds'][0],noweights=True,uvrange=uvrange)

    # make a mask from the final image
    make_mask('image_dirin_SSD.app.restored.fits',o['thresholds'][0],external_mask=external_mask)
    mask_dicomodel('image_dirin_SSD.DicoModel','image_dirin_SSD.app.restored.fits.mask.fits','image_dirin_SSD_masked.DicoModel')

    # cluster to get facets
    if not os.path.exists('image_dirin_SSD.Norm.fits'):
        os.symlink('image_dirin_SSD_init.Norm.fits','image_dirin_SSD.Norm.fits')
    if not os.path.exists('image_dirin_SSD.dirty.fits'):
        os.symlink('image_dirin_SSD_init.dirty.fits','image_dirin_SSD.dirty.fits')
    if make_model('image_dirin_SSD.app.restored.fits.mask.fits','image_dirin_SSD'):
        # if this step runs, clear the cache to remove facet info
        clearcache(o['mslist'],o['cache_dir'])

    if o['auto_uvmin']:
        killms_uvrange[0]=optimize_uvmin('image_dirin_SSD',o['mslist'],colname)

    killms_data('image_dirin_SSD',o['mslist'],'killms_p1',colname=colname,dicomodel='image_dirin_SSD_masked.DicoModel',clusterfile='image_dirin_SSD.npy.ClusterCat.npy',niterkf=o['NIterKF'][0],uvrange=killms_uvrange)

    # run bootstrap, and change the column name if it runs
    if o['bootstrap']:
        report('Running bootstrap')
        run('bootstrap.py '+sys.argv[1],log=None)
        colname='SCALED_DATA'

    # make the extended mask if required and possible
    if os.path.isfile('image_bootstrap.app.restored.fits') and o['extended_size'] is not None:
        if not(os.path.isfile('mask-high.fits')):
            report('Making the extended source mask')
            mask_base_image='image_bootstrap.app.restored.fits'
            make_extended_mask(mask_base_image,'image_dirin_SSD.app.restored.fits',rmsthresh=o['extended_rms'],sizethresh=o['extended_size'])
        else:
            warn('Extended source mask already exists, using existing version')
        external_mask='external_mask_ext.fits'
        make_external_mask(external_mask,'image_dirin_SSD_init.dirty.fits',use_tgss=True,clobber=False,extended_use='mask-high.fits')

    # Apply phase solutions and image again
    ddf_image('image_phase1',o['mslist'],cleanmask=external_mask,cleanmode='SSD',ddsols='killms_p1',applysols='P',majorcycles=4,robust=o['robust'],colname=colname,peakfactor=0.01,automask=True,automask_threshold=o['thresholds'][1],normalization=o['normalize'][0],uvrange=uvrange)

    make_mask('image_phase1.app.restored.fits',o['thresholds'][1],external_mask=external_mask)
    mask_dicomodel('image_phase1.DicoModel','image_phase1.app.restored.fits.mask.fits','image_phase1_masked.DicoModel')
    # Calibrate off the model
    if o['auto_uvmin']:
        killms_uvrange[0]=optimize_uvmin('image_dirin_SSD',o['mslist'],colname)

    killms_data('image_phase1',o['mslist'],'killms_ap1',colname=colname,dicomodel='image_phase1_masked.DicoModel',niterkf=o['NIterKF'][1],uvrange=killms_uvrange)

    # Apply phase and amplitude solutions and image again
    ddf_image('image_ampphase1',o['mslist'],cleanmask='image_phase1.app.restored.fits.mask.fits',cleanmode='SSD',ddsols='killms_ap1',applysols='AP',majorcycles=4,robust=o['robust'],colname=colname,use_dicomodel=True,dicomodel_base='image_phase1_masked',peakfactor=0.005,automask=True,automask_threshold=o['thresholds'][2],normalization=o['normalize'][1],uvrange=uvrange)
    make_mask('image_ampphase1.app.restored.fits',o['thresholds'][2],external_mask=external_mask)
    mask_dicomodel('image_ampphase1.DicoModel','image_ampphase1.app.restored.fits.mask.fits','image_ampphase1_masked.DicoModel')

    # Now move to the full dataset, if it exists
    if o['full_mslist'] is None:
        warn('No full MS list supplied, stopping here')
    else:
        # Check imaging weights -- needed before DDF
        check_imaging_weight(o['full_mslist'])

        if o['auto_uvmin']:
            killms_uvrange[0]=optimize_uvmin('image_dirin_SSD',o['mslist'],colname)
        killms_data('image_ampphase1',o['full_mslist'],'killms_f_ap1',colname=colname,clusterfile='image_dirin_SSD.npy.ClusterCat.npy',dicomodel='image_ampphase1_masked.DicoModel',niterkf=o['NIterKF'][2],uvrange=killms_uvrange)
        ddf_image('image_full_ampphase1',o['full_mslist'],cleanmask='image_ampphase1.app.restored.fits.mask.fits',cleanmode='SSD',ddsols='killms_f_ap1',applysols='AP',majorcycles=2,beamsize=o['final_psf_arcsec'],robust=o['final_robust'],colname=colname,use_dicomodel=True,dicomodel_base='image_ampphase1_masked',peakfactor=0.001,automask=True,automask_threshold=o['thresholds'][3],smooth=True,normalization=o['normalize'][2],uvrange=uvrange)
        make_mask('image_full_ampphase1.app.restored.fits',o['thresholds'][3],external_mask=external_mask)
        mask_dicomodel('image_full_ampphase1.DicoModel','image_full_ampphase1.app.restored.fits.mask.fits','image_full_ampphase1_masked.DicoModel')
        ddf_image('image_full_ampphase1m',o['full_mslist'],cleanmask='image_full_ampphase1.app.restored.fits.mask.fits',cleanmode='SSD',ddsols='killms_f_ap1',applysols='AP',majorcycles=2,beamsize=o['final_psf_arcsec'],robust=o['final_robust'],colname=colname,use_dicomodel=True,dicomodel_base='image_full_ampphase1_masked',peakfactor=0.001,automask=True,automask_threshold=o['thresholds'][3],smooth=True,normalization=o['normalize'][2],reuse_psf=True,dirty_from_resid=True,uvrange=uvrange)
        if o['low_psf_arcsec'] is not None:
            # low-res reimage requested
            uvrange=[o['image_uvmin'],2.5*206.0/o['low_psf_arcsec']]
            if o['low_imsize'] is not None:
                low_imsize=o['low_imsize'] # allow over-ride
            else:
                low_imsize=o['imsize']*o['cellsize']/o['low_cell']
            # if mask-low exists then use it
            if os.path.isfile('mask-low.fits') and low_imsize==o['bsimsize']:
                extmask='mask-low.fits'
            else:
                extmask=None
            ddf_image('image_full_low',o['full_mslist'],cleanmask=extmask,cleanmode='SSD',ddsols='killms_f_ap1',applysols='AP',majorcycles=5,robust=o['low_robust'],uvrange=uvrange,beamsize=o['low_psf_arcsec'],imsize=low_imsize,cellsize=o['low_cell'],peakfactor=0.001,smooth=True,automask=True,automask_threshold=5,normalization='Amp',colname=colname)
