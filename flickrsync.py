#!/usr/bin/python

import HTMLParser
import json
import os
import urllib
import argparse
import flickrapi
import httplib, urllib2
import sys
import pprint
import flickrsecrets
#constants:
EXT_IMAGE = ('jpg', 'png', 'jpeg')
EXT_VIDEO = ('avi', 'wmv', 'mov', 'mp4', '3gp', 'ogg', 'ogv', 'mts')

# Put your API & SECRET keys here
DEFAULT_PHOTO_ID = "12988000204" #used while photoset isn't complete

MAX_ATTEMPTS = 3


# wrapper around api functions
def httpErrorCatcher(func, *args, **kwargs):
    for i in xrange(0, MAX_ATTEMPTS):
        try:
            if kwargs:
                return func(*args, **kwargs)
            else:
                return func(*args)
            break
        except (httplib.BadStatusLine, urllib2.HTTPError, urllib2.URLError) as e:
            print ">>>>%s: %s, attempt no. %s" % (e, e.message, i)
            if i == (MAX_ATTEMPTS-1): # the last attempt
                raise
        #endtry
    #endfor 
#enddef

class Syncer(object):
    def __init__(self, args):
        self.args = args
        self.sync_path = args.sync_path.rstrip(os.sep) + os.sep
        if not os.path.exists(self.sync_path):
            print 'Sync path does not exists'
            exit(1)
        #endif
        
        self.is_windows = os.name == 'nt'
        print "Runnig sync in %s" % self.sync_path
        
        self.flickrargs = args = {'format': 'json', 'nojsoncallback': 1}
        self.api = flickrapi.FlickrAPI(flickrsecrets.KEY, flickrsecrets.SECRET)
        # api.token.path = 'flickr.token.txt'

        # Ask for permission
        (token, frob) = self.api.get_token_part_one(perms='write')

        if not token:
            raw_input("Please authorized this app then hit enter:")

        try:
            token = self.api.get_token_part_two((token, frob))
        except:
            print 'Please authorized to use'
            exit(1)

        self.flickrargs.update({'auth_token': token})
        
        # {localrelativedir : [files]}
        self.local_photos = {}
        # {description: id}
        self.remote_photosets = {}
    #enddef
    
    def read_local_photos(self):
        skips_root = []
        for r, dirs, files in os.walk(self.sync_path):
            files = [f for f in files if not f.startswith('.')]
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for file in files:
                if not file.startswith('.'):
                    ext = file.lower().split('.').pop()
                    if ext in EXT_IMAGE or ext in EXT_VIDEO:
                        if r == self.sync_path:
                            skips_root.append(file)
                        else:
                            localdir = r.replace(self.sync_path, "") #only relative dir
                            self.local_photos.setdefault(localdir, [])
                            self.local_photos[localdir].append(file)
                        #endif
                    #endf
            #endfor
        #endfor
        if skips_root:
            print 'To avoid disorganization on flickr sets root photos are not synced, skipped these photos:', skips_root
            print 'Try to sync at top most level of your photos directory'
    #enddef
   
    def read_remote_sets(self):
        html_parser = HTMLParser.HTMLParser()
        photosets_args = self.flickrargs.copy()
        page = 1
        self.remote_photosets = {}
        while True:
            print 'Getting photosets page %s' % page
            photosets_args.update({'page': page, 'per_page': 500})
            sets = json.loads(self.api.photosets_getList(**photosets_args))
            page += 1
            if not sets['photosets']['photoset']:
                break
            for theset in sets["photosets"]["photoset"]:
                desc = theset["description"]["_content"].encode("utf-8").split("\n")[0]
                id = theset["id"].encode("utf-8")
                if not desc in self.remote_photosets:
                    self.remote_photosets[desc] = id
            #endfor
        #endwhile
        #pprint.pprint(self.remote_photosets)
    #enddef
    
    def check_dirs_vs_sets(self):
        for localdir in sorted(self.local_photos.keys()):
            setId = self.remote_photosets.get(localdir, "")
            if setId:
                print "localdir %s paired to photoset %s" % (localdir, setId)
            else:
                print "localdir %s has no matching photoset - should be created" % localdir
                setId = self.create_set(localdir)
            #endif
            self.check_photos_in_set(localdir, setId)
        #endfor
    #enddef
    
    def get_photos_in_set(self, localdir, setId):
        # {filename:id}
        photos = {}
        photoset_args = self.flickrargs.copy()
        page = 1
        while True:
            photoset_args.update({"photoset_id" : setId, "page" : page})
            photos_in_set = json.loads(self.api.photosets_getPhotos(**photoset_args))
            page += 1
            if photos_in_set['stat'] != 'ok':
                break
            for photo in photos_in_set["photoset"]["photo"]:
                photos[photo["title"].encode("utf-8")] = photo["id"].encode("utf-8")
            #enfor
        #endwhile
        return photos
    #enddef
    
    def check_photos_in_set(self, localdir, setId):
        setphotos = self.get_photos_in_set(localdir, setId)
        #pprint.pprint(setphotos)
        firstPhotoId = ""
        for localphoto in sorted(self.local_photos[localdir]):
            localPhotoPath = os.path.join(localdir, localphoto)
            if localphoto in setphotos:
                print "photo %s already in set - skipping" % localPhotoPath
                continue;
            #endif
            print "missing file: %s; should be uploaded" % localPhotoPath
            photoId = self.upload_to_set(localPhotoPath, setId)
            if (not firstPhotoId) and photoId:
                firstPhotoId = photoId
        #endfor
        if firstPhotoId:
            primary_args = self.flickrargs.copy()
            primary_args.update({"photoset_id" : setId, "photo_id" : firstPhotoId})
            ffres = json.loads(self.api.photosets_setPrimaryPhoto(**primary_args))
            if ffres["stat"] == "ok":
                removedefault_args = self.flickrargs.copy()
                removedefault_args.update({"photoset_id" : setId, "photo_id" : DEFAULT_PHOTO_ID})
                self.api.photosets_removePhoto(**removedefault_args)
            print "Primary photo has been set"
    #enddef
    
    def upload_to_set(self, localPhotoPath, setId):
        photoId = self.upload_photo(localPhotoPath)
        if photoId:
            self.set_photo_to_set(photoId, setId)
        return photoId
    #enddef
        
    def upload_photo(self, localPhotoPath):
        if self.args.ignore_images and localPhotoPath.split('.').pop().lower() in EXT_IMAGE:
            print "Skipping '%s' because of ignore image is set" % localPhotoPath
            return ""
        elif self.args.ignore_videos and localPhotoPath.split('.').pop().lower() in EXT_VIDEO:
            print "Skipping '%s' because of ignore videos is set" % localPhotoPath
            return ""
        #endif
        file_stat = os.stat(localPhotoPath)
        if file_stat.st_size >= 1073741824:
            print "Skipped file '%s' over size limit. Size: %s" % (localPhotoPath, file_stat.st_size)
            return ""
        #endif
        
        title = localPhotoPath.split(os.sep)[-1]
        upload_args = {"auth_token" : self.flickrargs["auth_token"],
                       "title": title,
                       'hidden': 1, 'is_public': 0, 'is_friend': 0, 'is_family': 0}
        try:
            upload = httpErrorCatcher(self.api.upload, localPhotoPath, None, **upload_args)
            photoId = upload.find('photoid').text
            print "File '%s' has been uploaded under id: '%s'" % (localPhotoPath, photoId)
            return photoId
        except flickrapi.FlickrError as e:
            print e.message
    #enddef
    
    def set_photo_to_set(self, photoId, setId):
        photosets_args = self.flickrargs.copy()
        photosets_args.update({'photoset_id': setId, 'photo_id': photoId})
        result = json.loads(httpErrorCatcher(self.api.photosets_addPhoto, **photosets_args))
        if result.get('stat') == 'ok':
            print "Photo %s sucessfully added to set %s" % (photoId, setId)
        else:
            print "Error occured while adding photo %s to set %s: %s" % (photoId, setId, result)
    #enddef
    
    def create_set(self, localdir):
        if self.is_windows:
            localdir = localdir.replace(os.sep, "/")
        #endif
        title = localdir.replace("/", ":")
        description = localdir
        set_args = self.flickrargs.copy()
        set_args.update({'primary_photo_id': DEFAULT_PHOTO_ID,
                         'title': title,
                         'description': localdir})
        result_set = json.loads(httpErrorCatcher(self.api.photosets_create, **set_args))
        setId = result_set["photoset"]["id"].encode("utf-8")
        print "For localdir: '%s' a set has been created: '%s'" % (localdir, setId)
        return setId
    #enddef
   
    def sync(self):
        self.read_local_photos()
        self.read_remote_sets()
        self.check_dirs_vs_sets()
        print "Everything seems to be synced"
    #enddef
#endclass

def main():
    parser = argparse.ArgumentParser(description='Sync current folder to your flickr account.')
    parser.add_argument('--download', type=str, help='download the photos from flickr specify a path or . for all')
    parser.add_argument('--ignore-videos', action='store_true', help='ignore video files')
    parser.add_argument('--ignore-images', action='store_true', help='ignore image files')
    parser.add_argument('--sync-path', type=str, default=os.getcwd(), help='specify the sync folder (default is current dir)')

    args = parser.parse_args()
    syncer = Syncer(args)
    syncer.sync()
#enddef

if __name__ == "__main__":
    main()
    
