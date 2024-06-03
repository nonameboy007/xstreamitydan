#!/usr/bin/python
# -*- coding: utf-8 -*-

# https://mkvtoolnix.download/samples/
# https://github.com/OpenSPA/TVWeb/blob/master/usr/lib/enigma2/python/Plugins/Extensions/TVweb/plugin.py
# https://github.com/openatv/enigma2/blob/7.0/lib/python/Components/Task.py
# https://github.com/openatv/enigma2/blob/7.0/lib/python/Screens/TaskView.py
# https://forums.openpli.org/topic/52171-cancel-a-single-job-in-jobmanager-taskpy/
from __future__ import division

from . import _
from .plugin import skin_directory, downloads_json, cfg, pythonVer, hdr
from .xStaticText import StaticText
from .Task import job_manager as JobManager
from .Task import Task, Job

from Components.ActionMap import ActionMap
from Components.Sources.List import List
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from enigma import eTimer, eServiceReference
from requests.adapters import HTTPAdapter, Retry

try:
    from urlparse import urlparse
except:
    from urllib.parse import urlparse

import json
import math
import os
import re
import requests
import subprocess
import time

try:
    from http.client import HTTPConnection
    HTTPConnection.debuglevel = 0
except:
    from httplib import HTTPConnection
    HTTPConnection.debuglevel = 0

ui = False

temp1 = _("Not Started")
temp2 = _("In progress")
temp3 = _("Waiting")


def convert_size(size_bytes):
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return "%s %s" % (s, size_name[i])


class downloadJob(Job):
    def __init__(self, toolbox, cmdline, filename, filmtitle):
        Job.__init__(self, filmtitle)
        self.cmdline = cmdline
        self.filename = filename
        self.toolbox = toolbox
        self.retrycount = 0
        downloadTask(self, cmdline, filename, filmtitle)

    def retry(self):
        assert self.status == self.FAILED
        self.restart()

    def cancel(self):
        self.abort()


# downloadtask code borrowed from old video plugins
class downloadTask(Task):

    def __init__(self, job, cmdline, filename, filmtitle):
        Task.__init__(self, job, filmtitle)
        self.toolbox = job.toolbox
        self.setCmdline(cmdline)
        self.filename = filename
        self.filmtitle = filmtitle
        self.error = None
        self.lasterrormsg = None
        self.progress = 0
        self.lastprogress = 0
        self.firstrun = True
        self.starttime = time.time()

    def processOutput(self, data):
        global ui
        if pythonVer == 3:
            data = str(data)
        try:
            if data.find("%") != -1:
                tmpvalue = re.findall(r'(\d+?%)', data)[-1].rstrip("%")
                self.progress = int(float(tmpvalue))

                if self.firstrun:
                    self.firstrun = False
                    if ui:
                        self.toolbox.updatescreen()

                elif self.progress == 100:
                    self.lastprogress = int(self.progress)
                    if ui:
                        self.toolbox.updatescreen()

                elif int(self.progress) != int(self.lastprogress):
                    self.lastprogress = int(self.progress)

                    elapsed_time = time.time() - self.starttime
                    if ui and elapsed_time > 2:
                        self.starttime = time.time()
                        self.toolbox.updatescreen()

                else:
                    Task.processOutput(self, data)

        except Exception as errormsg:
            print("Error processOutput: " + str(errormsg))
            Task.processOutput(self, data)

    def processOutputLine(self, line):
        pass

    def afterRun(self):
        if self.getProgress() == 100 or self.progress == 100:
            try:
                self.toolbox.download_finished(self.filename, self.filmtitle)
            except Exception as e:
                print(e)


class XStreamity_DownloadManager(Screen):

    def __init__(self, session):
        global ui
        ui = True
        Screen.__init__(self, session)

        self.session = session

        self.setup_title = _("VOD Download Manager")
        self.onChangedEntry = []

        self.list = []
        self.drawList = []
        self.downloads_all = []

        self.progress = 0
        self.timerDisplay = eTimer()

        skin_path = os.path.join(skin_directory, cfg.skin.value)
        skin = os.path.join(skin_path, "downloadmanager.xml")

        with open(skin, "r") as f:
            self.skin = f.read()

        self["downloadlist"] = List(self.drawList, enableWrapAround=True)
        self["downloadlist"].onSelectionChanged.append(self.selectionChanged)

        self["key_red"] = StaticText(_("Back"))
        self["key_green"] = StaticText(_("Download"))
        self["key_blue"] = StaticText(_("Remove"))

        self["diskspace"] = StaticText()

        self["actions"] = ActionMap(["XStreamityActions"], {
            "red": self.keyCancel,
            "cancel": self.keyCancel,
            "green": self.download,
            "ok": self.download,
            "blue": self.delete,
        }, -2)

        self.onFirstExecBegin.append(self.start)
        self.onLayoutFinish.append(self.__layoutFinished)

    def __layoutFinished(self):
        self.setTitle(self.setup_title)

    def cleantitle(self, title):
        cleanName = re.sub(r'[^A-Za-z0-9\s-]', '', title)  # Remove special characters except whitespace and hyphen
        cleanName = re.sub(r'\s+', ' ', cleanName)  # Replace multiple spaces with a single space
        cleanName = cleanName.strip()  # Remove leading and trailing spaces
        cleanName = cleanName.replace(' ', '-')  # Replace spaces with hyphens
        cleanName = re.sub(r'[-]+', '-', cleanName)  # Replace multiple hyphens with a single hyphen
        return cleanName

    def start(self):
        self.readJsonFile()
        self.cleanalltitles()
        self.diskspace()
        self.getDownloadSize()
        self.checkactivedownloads()
        self.stopDownloads()

    def readJsonFile(self):
        self.downloads_all = []
        if os.path.isfile(downloads_json):
            try:
                with open(downloads_json, "r") as f:
                    self.downloads_all = json.load(f)
            except Exception as e:
                print("Error reading JSON file:", e)
                with open(downloads_json, "w") as f:
                    json.dump(self.downloads_all, f)
        else:
            print("Downloads JSON file does not exist. Creating...")
            with open(downloads_json, "w") as f:
                json.dump(self.downloads_all, f)

    def diskspace(self):
        try:
            stat = os.statvfs(cfg.downloadlocation.value)
            free_bytes = stat.f_bfree * stat.f_bsize
            total_bytes = stat.f_blocks * stat.f_bsize
            free_space = convert_size(float(free_bytes))
            total_space = convert_size(float(total_bytes))
            self["diskspace"].setText(_("Free Space:") + " " + str(free_space) + " " + _("of") + " " + str(total_space))
        except Exception as e:
            print("Error getting disk space:", e)
            self["diskspace"].setText(_("Free Space:") + " -?- " + _("of") + " -?-")

    def cleanalltitles(self):
        for video in self.downloads_all:
            title = video[1]
            cleaned_title = self.cleantitle(title)
            video[1] = cleaned_title

    def getDownloadSize(self):
        x = 0
        for video in self.downloads_all:
            if video[5] == 0:
                url = video[2]

                retries = Retry(total=3, backoff_factor=1)
                adapter = HTTPAdapter(max_retries=retries)
                http = requests.Session()
                http.mount("http://", adapter)
                http.mount("https://", adapter)

                try:
                    with http.get(url, headers=hdr, timeout=10, verify=False, stream=True) as r:
                        r.raise_for_status()
                        if r.status_code == requests.codes.ok:
                            content_length = float(r.headers.get("content-length", 0))
                            video[5] = content_length

                except Exception as e:
                    print(e)
                    video[5] = 0  # Set download size to 0 in case of any error

                x += 1
                if x == 5:
                    x = 0
                    time.sleep(1)

    def checkactivedownloads(self):
        templist = []

        for video in self.downloads_all:
            recbytes = 0
            filmtitle = str(video[1])
            extension = os.path.splitext(video[2])[-1]

            filename = filmtitle + extension
            path = os.path.join(cfg.downloadlocation.value, filename)

            totalbytes = video[5]

            if os.path.exists(path):
                recbytes = os.path.getsize(path)
                if int(totalbytes) != int(recbytes):
                    try:
                        progress = int((recbytes / totalbytes) * 100) - 2
                    except ZeroDivisionError:
                        progress = 0

                    if progress < 0:
                        progress = 0

                    video[4] = progress

                    templist.append(video)
            else:
                video[3] = "Not Started"
                video[4] = 0
                templist.append(video)

        self.downloads_all[:] = templist
        self.buildList()
        self.saveJson()

    def stopDownloads(self):
        # Stop all active tasks related to XStreamity
        for job in JobManager.getPendingJobs():
            if "XStreamity" in job.cmdline:
                if job.status == job.NOT_STARTED:
                    JobManager.active_jobs.remove(job)
                elif job.status == job.IN_PROGRESS:
                    job.cancel()

        self.resumeDownloads()

    def resumeDownloads(self):
        for video in self.downloads_all:
            filmtitle = str(video[1])
            url = str(video[2])
            state = str(video[3])

            try:
                extension = os.path.splitext(url)[-1]
            except Exception as e:
                print(e)
                extension = ""

            filename = str(filmtitle) + str(extension)
            shortpath = str(cfg.downloadlocation.value)
            path = os.path.join(cfg.downloadlocation.value, filename)

            parsed_uri = urlparse(url)
            video_domain = parsed_uri.hostname

            if state != "Not Started":
                if self.session.nav.getCurrentlyPlayingServiceReference():
                    playingstream = self.session.nav.getCurrentlyPlayingServiceReference().toString()
                    if video_domain and str(video_domain) in playingstream:
                        if self.session.nav.getCurrentlyPlayingServiceReference():
                            self.session.nav.stopService()

                cmd = "wget -U 'Enigma2 - XStreamity Plugin' -c '%s' -O '%s%s'" % (url, shortpath, filename)
                if "https" in str(url):
                    cmd = "wget --no-check-certificate -U 'Enigma2 - XStreamity Plugin' -c '%s' -O '%s%s'" % (url, shortpath, filename)

                try:
                    JobManager.AddJob(downloadJob(self, cmd, path, filmtitle), onFail=self.fail)
                except Exception as e:
                    print(e)

        self.updatescreen()

    def fail(self, job=None, task=None, problems=None):
        return False

    def buildList(self):
        self.drawList = []
        self.drawList = [self.buildListEntry(x[0], x[1], x[2], str(x[3]), x[4], x[5]) for x in self.downloads_all]
        self["downloadlist"].setList(self.drawList)

    def updatescreen(self):
        self.diskspace()
        self.getprogress()

    def sortlist(self):
        order = {"In progress": 0, "Waiting": 1, "Not Started": 2}
        self.downloads_all.sort(key=lambda x: order.get(x[3], 3))  # Use get() with a default value of 3 for unmatched states

    def getprogress(self):
        jobs = JobManager.getPendingJobs()
        if len(jobs) >= 1:
            for job in jobs:
                if "XStreamity" in job.cmdline:
                    jobname = str(job.name)
                    for video in self.downloads_all:
                        title = str(video[1])
                        if title == jobname:
                            if job.status == job.NOT_STARTED:
                                video[3] = "Waiting"

                            elif job.status == job.IN_PROGRESS:
                                video[3] = "In progress"

                            video[4] = job.progress
                            self.buildList()
                            break

    def saveJson(self):
        with open(downloads_json, "w") as f:
            json.dump(self.downloads_all, f)

    def selectionChanged(self):
        current_item = self["downloadlist"].getCurrent()
        if current_item:
            state = current_item[3]
            self["key_green"].setText(_("Cancel") if state != _("Not Started") else _("Download"))
            self["key_blue"].setText("") if state != _("Not Started") else _("Remove")

    def keyCancel(self, answer=None):
        global ui
        ui = False
        self.saveJson()
        self.close()

    def download(self):
        if not os.path.exists(cfg.downloadlocation.value) or cfg.downloadlocation.value is None:
            self.session.open(MessageBox, _("Vod Download folder location does not exist.\n\n" + str(cfg.downloadlocation.value) + _("Please set download folder in Main Settings.")), type=MessageBox.TYPE_WARNING)
            return

        if self["downloadlist"].getCurrent():

            self.filmtitle = self["downloadlist"].getCurrent()[1]

            self.url = self["downloadlist"].getCurrent()[2]

            try:
                self.extension = str(os.path.splitext(self.url)[-1])
            except:
                self.extension = ""

            filename = str(self.filmtitle) + str(self.extension)
            self.shortpath = str(cfg.downloadlocation.getValue())
            self.path = os.path.join(cfg.downloadlocation.getValue(), filename)

            parsed_uri = urlparse(self.url)
            video_domain = parsed_uri.hostname

            if self["downloadlist"].getCurrent()[3] == _("Not Started"):

                if self.session.nav.getCurrentlyPlayingServiceReference():
                    playingstream = self.session.nav.getCurrentlyPlayingServiceReference().toString()

                    if str(video_domain) in playingstream:
                        # stop iptv
                        if self.session.nav.getCurrentlyPlayingServiceReference():
                            self.session.nav.stopService()

                cmd = "wget -U 'Enigma2 - XStreamity Plugin' -c '%s' -O '%s%s'" % (self.url, self.shortpath, filename)

                if "https" in str(self.url):
                    checkcmd = "strings $(which wget) | grep no-check-certificate"
                    if pythonVer == 2:
                        result = subprocess.call(checkcmd, shell=True)
                        if result == 0:
                            cmd = "wget --no-check-certificate -U 'Enigma2 - XStreamity Plugin' -c '%s' -O '%s%s'" % (self.url, self.shortpath, filename)
                        else:
                            self.session.open(MessageBox, _("Please update your wget library to download https lines\n\nopkg update\nopkg install wget"), type=MessageBox.TYPE_INFO)
                    else:
                        result = subprocess.run(checkcmd, shell=True)
                        if result.returncode == 0:
                            cmd = "wget --no-check-certificate -U 'Enigma2 - XStreamity Plugin' -c '%s' -O '%s%s'" % (self.url, self.shortpath, filename)
                        else:
                            self.session.open(MessageBox, _("Please update your wget library to download https lines\n\nopkg update\nopkg install wget"), type=MessageBox.TYPE_INFO)

                try:
                    JobManager.AddJob(downloadJob(self, cmd, self.path, self.filmtitle), onFail=self.fail)
                except Exception as e:
                    print(e)

                try:
                    self.updatescreen()
                    self.sortlist()
                    self.buildList()
                    self["downloadlist"].setIndex(0)
                    self.saveJson()
                except Exception as e:
                    print(e)

            else:
                self.cancelConfirm()

    def cancelConfirm(self, answer=None):
        if answer is None:
            self.session.openWithCallback(self.cancelConfirm, MessageBox, _("Cancel this download?"))
        elif answer:
            self.cancelJob()
        else:
            return

    def cancelJob(self, answer=None):
        jobs = JobManager.getPendingJobs()

        for job in jobs:
            jobname = str(job.name)
            if self.filmtitle == jobname:

                for video in self.downloads_all:
                    if str(video[1]) == str(self.filmtitle):
                        video[3] = "Not Started"
                        self.buildList()
                        self.saveJson()

                        if job.status == job.NOT_STARTED:
                            JobManager.active_jobs.remove(job)

                        elif job.status == job.IN_PROGRESS:
                            job.cancel()

    def delete(self):
        if self["downloadlist"].getCurrent():
            currentindex = self["downloadlist"].getIndex()
            if self.downloads_all[currentindex][3] == "Not Started":
                self.delete_entry()

    def delete_entry(self, answer=None):
        if answer is None:
            self.session.openWithCallback(self.delete_entry, MessageBox, _("Delete this entry?"))
        elif answer:
            currentindex = self["downloadlist"].getIndex()
            del self.downloads_all[currentindex]
            self.sortlist()
            self.buildList()
            self.saveJson()

    def createMetaFile(self, filename, filmtitle):
        try:
            serviceref = eServiceReference(4097, 0, filename)
            with open("%s.meta" % (filename), "w") as f:
                f.write("%s\n%s\n%s\n%i\n" % (serviceref.toString(), filmtitle, "", time.time()))
        except Exception as e:
            print(e)
        return

    def download_finished(self, filename, filmtitle):
        global ui
        if os.path.isfile(downloads_json):
            with open(downloads_json, "r") as f:
                try:
                    self.downloads_all = json.load(f)
                except Exception as e:
                    print(e)

        x = 0
        for video in self.downloads_all:
            if str(video[1]) == str(filmtitle):
                break
            x += 1
        del self.downloads_all[x]

        if ui:
            self.sortlist()
            self.buildList()

        self.createMetaFile(filename, self.filmtitle)
        self.saveJson()

    def buildListEntry(self, dtype, title, url, state, progress, length):
        progresspercent = str(progress) + "%"
        length = convert_size(length)
        return (str(dtype), str(title), str(url), _(state), int(progress), str(progresspercent), str(length))
