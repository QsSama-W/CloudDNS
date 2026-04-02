import sys
import json
import re
import os
import requests
import datetime
import threading
import webbrowser
import socket
import tkinter as tk
from tkinter import messagebox
from flask import Flask, request, jsonify, send_file

# ==========================================
# 云厂商 SDK 导入区域
# ==========================================
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.acs_exception.exceptions import ClientException, ServerException
from aliyunsdkalidns.request.v20150109 import (DescribeDomainsRequest, 
                                              DescribeSubDomainRecordsRequest,
                                              DescribeDomainRecordsRequest,
                                              UpdateDomainRecordRequest,
                                              AddDomainRecordRequest,
                                              DeleteDomainRecordRequest,
                                              SetDomainRecordStatusRequest)

try:
    from tencentcloud.common import credential
    from tencentcloud.dnspod.v20210323 import dnspod_client, models as tc_models
    TC_SDK_INSTALLED = True
except ImportError:
    TC_SDK_INSTALLED = False

CURRENT_VERSION = "v1.0.0"
GITHUB_REPO_URL = "https://github.com/QsSama-W/CloudDNS"
RELEASES_URL = "https://api.github.com/repos/QsSama-W/CloudDNS/releases/latest"

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_PATH = os.path.join(APP_DIR, 'AccessKey.json')

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

FLASK_PORT = get_free_port()

# ==========================================
# 核心业务逻辑
# ==========================================
class AliyunDNSClient:
    def __init__(self, access_key_id, access_key_secret, region_id="cn-hangzhou"):
        self.client = AcsClient(ak=access_key_id, secret=access_key_secret, region_id=region_id)
    
    def get_domains(self):
        req = DescribeDomainsRequest.DescribeDomainsRequest()
        req.set_accept_format('json'); req.set_PageSize(100)
        res = json.loads(self.client.do_action_with_exception(req))
        return [d.get('DomainName') for d in res.get('Domains', {}).get('Domain', [])] if res.get('TotalCount', 0) > 0 else []
    
    def get_record_id(self, main_domain, sub_domain, record_type):
        full = f"{sub_domain}.{main_domain}" if sub_domain != '@' else main_domain
        req = DescribeSubDomainRecordsRequest.DescribeSubDomainRecordsRequest()
        req.set_accept_format('json'); req.set_SubDomain(full); req.set_Type(record_type)
        res = json.loads(self.client.do_action_with_exception(req))
        return res.get('DomainRecords', {}).get('Record', [{}])[0].get('RecordId') if res.get('TotalCount', 0) > 0 else None
    
    def get_domain_records(self, main_domain):
        req = DescribeDomainRecordsRequest.DescribeDomainRecordsRequest()
        req.set_accept_format('json'); req.set_DomainName(main_domain); req.set_PageSize(100)
        res = json.loads(self.client.do_action_with_exception(req))
        records = []
        for r in res.get('DomainRecords', {}).get('Record', []):
            if r.get('Type') in ['A', 'AAAA', 'TXT', 'CNAME']:
                rr = r.get('RR', '@')
                full = f"{rr}.{main_domain}" if rr != '@' else main_domain
                ts = r.get('UpdateTimestamp')
                dt = datetime.datetime.fromtimestamp(ts/1000).strftime('%Y-%m-%d %H:%M:%S') if ts else "-"
                records.append({
                    'full_domain': full, 'rr': rr, 'type': r.get('Type'), 'value': r.get('Value', ''),
                    'record_id': r.get('RecordId'), 'status': r.get('Status', 'ENABLE'), 'update_time': dt,
                    'provider': 'aliyun', 'is_pages': False
                })
        return records
    
    def add_or_update(self, main, sub, val, rtype):
        rid = self.get_record_id(main, sub, rtype)
        if rid:
            req = UpdateDomainRecordRequest.UpdateDomainRecordRequest()
            req.set_accept_format('json'); req.set_RecordId(rid); req.set_RR(sub); req.set_Type(rtype); req.set_Value(val)
            self.client.do_action_with_exception(req)
            return "更新"
        else:
            req = AddDomainRecordRequest.AddDomainRecordRequest()
            req.set_accept_format('json'); req.set_DomainName(main); req.set_RR(sub); req.set_Type(rtype); req.set_Value(val)
            self.client.do_action_with_exception(req)
            return "添加"

    def set_status(self, rid, status):
        req = SetDomainRecordStatusRequest.SetDomainRecordStatusRequest()
        req.set_accept_format('json'); req.set_RecordId(rid); req.set_Status(status)
        self.client.do_action_with_exception(req)

    def delete(self, rid):
        req = DeleteDomainRecordRequest.DeleteDomainRecordRequest()
        req.set_accept_format('json'); req.set_RecordId(rid)
        self.client.do_action_with_exception(req)

class TencentDNSClient:
    def __init__(self, secret_id, secret_key):
        if not TC_SDK_INSTALLED:
            raise Exception("系统缺失腾讯云SDK，请在终端执行: pip install tencentcloud-sdk-python")
        cred = credential.Credential(secret_id, secret_key)
        self.client = dnspod_client.DnspodClient(cred, "")
        
    def get_domains(self):
        req = tc_models.DescribeDomainListRequest()
        resp = self.client.DescribeDomainList(req)
        return [d.Name for d in resp.DomainList] if resp.DomainList else []
        
    def get_domain_records(self, domain):
        req = tc_models.DescribeRecordListRequest()
        req.Domain = domain
        req.Limit = 3000
        resp = self.client.DescribeRecordList(req)
        records = []
        if resp.RecordList:
            for r in resp.RecordList:
                if r.Type in ['A', 'AAAA', 'TXT', 'CNAME']:
                    full = f"{r.Name}.{domain}" if r.Name != '@' else domain
                    records.append({
                        'full_domain': full, 'rr': r.Name, 'type': r.Type, 'value': r.Value,
                        'record_id': r.RecordId, 'status': 'ENABLE' if r.Status == 'ENABLE' else 'DISABLE',
                        'update_time': r.UpdatedOn, 'provider': 'tencent', 'is_pages': False
                    })
        return records
        
    def add_or_update(self, domain, sub, val, rtype):
        req = tc_models.DescribeRecordListRequest()
        req.Domain = domain; req.Subdomain = sub; req.RecordType = rtype
        resp = self.client.DescribeRecordList(req)
        
        if resp.RecordList:
            rid = resp.RecordList[0].RecordId
            ureq = tc_models.ModifyRecordRequest()
            ureq.Domain = domain; ureq.RecordId = rid; ureq.SubDomain = sub; ureq.RecordType = rtype; ureq.RecordLine = "默认"; ureq.Value = val
            self.client.ModifyRecord(ureq)
            return "更新"
        else:
            areq = tc_models.CreateRecordRequest()
            areq.Domain = domain; areq.SubDomain = sub; areq.RecordType = rtype; areq.RecordLine = "默认"; areq.Value = val
            self.client.CreateRecord(areq)
            return "添加"
            
    def set_status(self, domain, rid, status):
        req = tc_models.ModifyRecordStatusRequest()
        req.Domain = domain; req.RecordId = rid; req.Status = status
        self.client.ModifyRecordStatus(req)
        
    def delete(self, domain, rid):
        req = tc_models.DeleteRecordRequest()
        req.Domain = domain; req.RecordId = rid
        self.client.DeleteRecord(req)

class CloudflareDNSClient:
    def __init__(self, api_token):
        self.api_token = api_token
        self.headers = {"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"}
        self.base_url = "https://api.cloudflare.com/client/v4"

    def _req(self, method, endpoint, data=None):
        url = f"{self.base_url}{endpoint}"
        res = requests.request(method, url, headers=self.headers, json=data).json()
        if not res.get('success'): raise Exception(res.get('errors', [{}])[0].get('message', 'API Error'))
        return res

    def get_domains(self):
        res = self._req("GET", "/zones?per_page=100")
        doms, zones = [], {}
        for z in res.get("result", []):
            doms.append(z.get('name')); zones[z.get('name')] = z.get('id')
        return doms, zones

    def get_domain_records(self, zone_id, main_domain):
        res = self._req("GET", f"/zones/{zone_id}/dns_records?per_page=100")
        records = []
        for r in res.get("result", []):
            if r.get('type') in ['A', 'AAAA', 'TXT', 'CNAME']:
                full = r.get('name', '')
                rr = '@' if full == main_domain else full.replace(f".{main_domain}", "")
                raw_time = r.get('modified_on', '')
                if raw_time:
                    try:
                        utc_dt = datetime.datetime.strptime(raw_time[:19], '%Y-%m-%dT%H:%M:%S')
                        local_dt = utc_dt + datetime.timedelta(hours=8)
                        dt = local_dt.strftime('%Y-%m-%d %H:%M:%S')
                    except Exception:
                        dt = raw_time
                else:
                    dt = "-"
                content = r.get('content', '')
                is_pages = ".pages.dev" in content or content in ["100::", "192.0.2.1"]
                records.append({
                    'full_domain': full, 'rr': rr, 'type': r.get('type'), 'value': content,
                    'record_id': r.get('id'), 'status': "PROXIED" if r.get('proxied') else "DNS_ONLY",
                    'update_time': dt, 'provider': 'cf', 'zone_id': zone_id,
                    'proxied': r.get('proxied', False), 'is_pages': is_pages
                })
        return records

    def add_or_update(self, zone_id, main, full, val, rtype):
        res = self._req("GET", f"/zones/{zone_id}/dns_records?name={full}&type={rtype}")
        exist = res.get('result', [None])[0] if len(res.get('result', [])) > 0 else None
        payload = {"type": rtype, "name": full, "content": val, "proxied": exist.get('proxied', False) if exist else False}
        if exist:
            self._req("PUT", f"/zones/{zone_id}/dns_records/{exist['id']}", payload)
            return "更新"
        else:
            self._req("POST", f"/zones/{zone_id}/dns_records", payload)
            return "添加"

    def set_status(self, zone_id, rid, full, val, rtype, proxied):
        self._req("PUT", f"/zones/{zone_id}/dns_records/{rid}", {"type": rtype, "name": full, "content": val, "proxied": proxied})

    def delete(self, zone_id, rid):
        self._req("DELETE", f"/zones/{zone_id}/dns_records/{rid}")

# ==========================================
# Flask Web 后端 API
# ==========================================
app = Flask(__name__)

def read_config():
    if not os.path.exists(CONFIG_PATH): return {}
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f: return json.load(f)

@app.route('/')
def index():
    html = HTML_CONTENT.replace('__APP_VERSION__', CURRENT_VERSION).replace('__GITHUB_REPO_URL__', GITHUB_REPO_URL)
    return html

@app.route('/api/ping')
def ping():
    return jsonify({"status": "alive"})

@app.route('/favicon.ico')
def favicon():
    ico_path = os.path.join(APP_DIR, 'images', 'logo.ico')
    png_path = os.path.join(APP_DIR, 'images', 'logo.png')
    if os.path.exists(ico_path): return send_file(ico_path, mimetype='image/vnd.microsoft.icon')
    elif os.path.exists(png_path): return send_file(png_path, mimetype='image/png')
    return "", 404

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    if request.method == 'POST':
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f: json.dump(request.json, f, indent=2)
        return jsonify({"success": True, "message": "配置已保存"})
    return jsonify({"success": True, "data": read_config()})

@app.route('/api/domains')
def api_domains():
    provider = request.args.get('provider')
    conf = read_config()
    try:
        if provider == 'aliyun':
            cli = AliyunDNSClient(conf.get('access_key_id',''), conf.get('access_key_secret',''), conf.get('region_id', 'cn-hangzhou'))
            doms = cli.get_domains()
            return jsonify({"success": True, "domains": doms, "zones": {}})
        elif provider == 'tencent':
            cli = TencentDNSClient(conf.get('tc_secret_id',''), conf.get('tc_secret_key',''))
            doms = cli.get_domains()
            return jsonify({"success": True, "domains": doms, "zones": {}})
        else:
            cli = CloudflareDNSClient(conf.get('cf_api_token',''))
            doms, zones = cli.get_domains()
            return jsonify({"success": True, "domains": doms, "zones": zones})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/api/records')
def api_records():
    provider = request.args.get('provider')
    domain = request.args.get('domain')
    zone_id = request.args.get('zone_id')
    conf = read_config()
    try:
        if provider == 'aliyun':
            cli = AliyunDNSClient(conf.get('access_key_id',''), conf.get('access_key_secret',''), conf.get('region_id', 'cn-hangzhou'))
            return jsonify({"success": True, "records": cli.get_domain_records(domain)})
        elif provider == 'tencent':
            cli = TencentDNSClient(conf.get('tc_secret_id',''), conf.get('tc_secret_key',''))
            return jsonify({"success": True, "records": cli.get_domain_records(domain)})
        else:
            cli = CloudflareDNSClient(conf.get('cf_api_token',''))
            return jsonify({"success": True, "records": cli.get_domain_records(zone_id, domain)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/api/records', methods=['POST'])
def api_add_record():
    d = request.json
    conf = read_config()
    record_value = d.get('value', d.get('val', ''))
    full = f"{d['sub']}.{d['domain']}" if d['sub'] != '@' else d['domain']
    
    try:
        if d['provider'] == 'aliyun':
            cli = AliyunDNSClient(conf.get('access_key_id',''), conf.get('access_key_secret',''), conf.get('region_id', 'cn-hangzhou'))
            action = cli.add_or_update(d['domain'], d['sub'], record_value, d['type'])
        elif d['provider'] == 'tencent':
            cli = TencentDNSClient(conf.get('tc_secret_id',''), conf.get('tc_secret_key',''))
            action = cli.add_or_update(d['domain'], d['sub'], record_value, d['type'])
        else:
            cli = CloudflareDNSClient(conf.get('cf_api_token',''))
            action = cli.add_or_update(d['zone_id'], d['domain'], full, record_value, d['type'])
        return jsonify({"success": True, "message": f"成功{action}解析记录: {full}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/api/records/status', methods=['POST'])
def api_status():
    d = request.json
    conf = read_config()
    try:
        if d['provider'] == 'aliyun':
            cli = AliyunDNSClient(conf.get('access_key_id',''), conf.get('access_key_secret',''), conf.get('region_id', 'cn-hangzhou'))
            cli.set_status(d['record_id'], d['status'])
        elif d['provider'] == 'tencent':
            cli = TencentDNSClient(conf.get('tc_secret_id',''), conf.get('tc_secret_key',''))
            cli.set_status(d['domain'], d['record_id'], d['status'])
        else:
            cli = CloudflareDNSClient(conf.get('cf_api_token',''))
            cli.set_status(d['zone_id'], d['record_id'], d['full_domain'], d['value'], d['type'], d['status'])
        return jsonify({"success": True, "message": "状态切换成功"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/api/records', methods=['DELETE'])
def api_delete():
    d = request.json
    conf = read_config()
    try:
        if d['provider'] == 'aliyun':
            cli = AliyunDNSClient(conf.get('access_key_id',''), conf.get('access_key_secret',''), conf.get('region_id', 'cn-hangzhou'))
            cli.delete(d['record_id'])
        elif d['provider'] == 'tencent':
            cli = TencentDNSClient(conf.get('tc_secret_id',''), conf.get('tc_secret_key',''))
            cli.delete(d['domain'], d['record_id'])
        else:
            cli = CloudflareDNSClient(conf.get('cf_api_token',''))
            cli.delete(d['zone_id'], d['record_id'])
        return jsonify({"success": True, "message": "记录已删除"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

def run_flask():
    app.run(host='127.0.0.1', port=FLASK_PORT, debug=False, use_reloader=False)

# ==========================================
# 内嵌前端 HTML/JS/CSS (Vue3 + Tailwind)
# ==========================================
HTML_CONTENT = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CloudDNS 控制台</title>
    <link rel="icon" type="image/x-icon" href="/favicon.ico">
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
    <style>
        body { background-color: #f8fafc; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-thumb { background-color: #cbd5e1; border-radius: 3px; }
        #offline-overlay { display: none; position: fixed; inset: 0; background: #0f172a; color: white; z-index: 9999; flex-direction: column; justify-content: center; align-items: center; }
    </style>
</head>
<body>

<div id="offline-overlay">
    <svg class="w-20 h-20 text-slate-500 mb-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
    <h1 class="text-3xl font-black mb-2">服务已安全断开</h1>
    <p class="text-slate-400">主程序已关闭，您可以直接关闭此浏览器标签页。</p>
</div>

<div id="app" class="min-h-screen flex flex-col md:flex-row text-slate-800">
    
    <div class="w-full md:w-64 bg-slate-900 text-white flex flex-col shadow-xl z-10">
        <div class="p-6">
            <h1 @click="goHome" class="text-2xl font-black bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-emerald-400 cursor-pointer transition hover:opacity-80">CloudDNS</h1>
            <p class="text-slate-400 text-xs mt-1">__APP_VERSION__</p>
        </div>
        <div class="px-4 flex-1">
            <p class="text-xs font-bold text-slate-500 mb-2 px-2">工作区 WORKSPACES</p>
            <button @click="switchProvider('aliyun')" :class="['w-full text-left px-4 py-2.5 rounded-lg mb-2 font-medium transition flex items-center gap-3', provider==='aliyun' ? 'bg-blue-600 text-white' : 'hover:bg-slate-800 text-slate-300']">
                <div class="flex items-center justify-center w-6 h-6 rounded-full bg-white overflow-hidden flex-shrink-0 p-0.5">
                    <img src="https://img.alicdn.com/tfs/TB1_ZXuNcfpK1RjSZFOXXa6nFXa-32-32.ico" alt="Aliyun" class="w-full h-full object-contain">
                </div>
                <span class="text-sm">阿里云 DNS</span>
            </button>

            <button @click="switchProvider('tencent')" :class="['w-full text-left px-4 py-2.5 rounded-lg mb-2 font-medium transition flex items-center gap-3', provider==='tencent' ? 'bg-cyan-600 text-white' : 'hover:bg-slate-800 text-slate-300']">
                <div class="flex items-center justify-center w-6 h-6 rounded-full bg-white overflow-hidden flex-shrink-0 p-0.5">
                    <img src="https://cloudcache.tencent-cloud.com/open_proj/proj_qcloud_v2/tc-console/dnspod/gateway/css/img/dnspod.ico" alt="DNSPod" class="w-full h-full object-contain">
                </div>
                <span class="text-sm">腾讯云 DNSPod</span>
            </button>

            <button @click="switchProvider('cf')" :class="['w-full text-left px-4 py-2.5 rounded-lg mb-2 font-medium transition flex items-center gap-3', provider==='cf' ? 'bg-orange-600 text-white' : 'hover:bg-slate-800 text-slate-300']">
                <div class="flex items-center justify-center w-6 h-6 rounded-full bg-white overflow-hidden flex-shrink-0 p-1">
                    <img src="https://www.cloudflare.com/img/favicon.ico" alt="Cloudflare" class="w-full h-full object-contain">
                </div>
                <span class="text-sm">Cloudflare</span>
            </button>
            
            <p class="text-xs font-bold text-slate-500 mt-8 mb-2 px-2">系统 SYSTEM</p>
            <button @click="showConfig = true" class="w-full text-left px-4 py-3 rounded-lg text-slate-300 hover:bg-slate-800 font-medium transition mb-2 flex items-center gap-2">
                <span>⚙️</span> API 密钥配置
            </button>
            <button @click="showAbout = true" class="w-full text-left px-4 py-3 rounded-lg text-slate-300 hover:bg-slate-800 font-medium transition flex items-center gap-2">
                <span>ℹ️</span> 关于 & 帮助
            </button>
        </div>
    </div>

    <div class="flex-1 p-4 md:p-8 overflow-auto flex flex-col gap-6 relative">
        
        <div v-if="provider === 'home'" class="flex-1 flex flex-col items-center justify-center text-center p-8 bg-white rounded-2xl shadow-sm border border-slate-200">
            <div class="bg-blue-50 p-6 rounded-full mb-8">
                <svg class="w-24 h-24 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"></path></svg>
            </div>
            <h2 class="text-3xl font-black text-slate-800 mb-4">欢迎使用 CloudDNS 管理控制台</h2>
            <p class="text-slate-500 max-w-lg mb-10 leading-relaxed">提供专业、极速的 阿里云、腾讯云 与 Cloudflare 解析记录管理。请在左侧选择您的工作区，或点击下方按钮开始使用。</p>
            <div class="flex gap-4">
                <button @click="switchProvider('aliyun')" class="px-6 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-bold transition shadow-lg shadow-blue-200">阿里云</button>
                <button @click="switchProvider('tencent')" class="px-6 py-3 bg-cyan-600 hover:bg-cyan-700 text-white rounded-xl font-bold transition shadow-lg shadow-cyan-200">腾讯云</button>
                <button @click="switchProvider('cf')" class="px-6 py-3 bg-orange-500 hover:bg-orange-600 text-white rounded-xl font-bold transition shadow-lg shadow-orange-200">Cloudflare</button>
            </div>
        </div>

        <template v-else>
            <div class="bg-white rounded-xl shadow-sm p-4 md:p-6 border border-slate-200 flex flex-col md:flex-row gap-4 items-center justify-between">
                <div class="flex items-center gap-4 w-full md:w-auto">
                    <span class="font-bold text-slate-500 whitespace-nowrap">管理域名</span>
                    <select v-model="currentDomain" @change="loadRecords" class="bg-slate-50 border border-slate-300 text-slate-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block w-full p-2.5 font-bold cursor-pointer">
                        <option v-for="d in domains" :value="d">{{ d }}</option>
                        <option v-if="domains.length === 0" value="">请先加载域名列表...</option>
                    </select>
                </div>
                <button @click="loadDomains" class="bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold py-2.5 px-5 rounded-lg border border-slate-300 transition shadow-sm w-full md:w-auto whitespace-nowrap">
                    ↻ 同步云端路由
                </button>
            </div>

            <div class="bg-white rounded-xl shadow-sm p-4 md:p-6 border border-slate-200">
                <h2 class="text-sm font-bold text-slate-500 mb-4 uppercase tracking-wider">快速添加解析</h2>
                <div class="flex flex-col md:flex-row gap-4">
                    <div class="relative flex-1">
                        <input v-model="form.sub" placeholder="主机记录 (如 www, @)" class="w-full bg-slate-50 border border-slate-300 text-slate-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block p-2.5 pr-8">
                        <svg v-if="form.sub.length > 0" @click="form.sub=''" class="w-4 h-4 text-slate-400 hover:text-slate-600 cursor-pointer absolute right-2.5 top-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                    </div>
                    
                    <select v-model="form.type" class="md:w-32 bg-slate-50 border border-slate-300 text-slate-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block p-2.5 font-bold cursor-pointer">
                        <option>A</option><option>AAAA</option><option>CNAME</option><option>TXT</option>
                    </select>
                    
                    <div class="relative flex-[2]">
                        <input v-model="form.val" placeholder="记录值 (IP / CNAME 目标 / 文本)" class="w-full bg-slate-50 border border-slate-300 text-slate-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block p-2.5 pr-8">
                        <svg v-if="form.val.length > 0" @click="form.val=''" class="w-4 h-4 text-slate-400 hover:text-slate-600 cursor-pointer absolute right-2.5 top-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                    </div>

                    <button @click="addRecord" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2.5 px-6 rounded-lg transition shadow-sm w-full md:w-auto whitespace-nowrap" :disabled="loading">
                        <span v-if="loading">执行中...</span><span v-else>+ 下发记录</span>
                    </button>
                </div>
                <p class="mt-2 text-xs font-medium" :class="ipVersionColor">{{ ipVersionText }}</p>
            </div>

            <div class="bg-white rounded-xl shadow-sm border border-slate-200 flex-1 overflow-hidden flex flex-col">
                <div class="overflow-x-auto flex-1">
                    <table class="w-full text-sm text-left">
                        <thead class="text-xs text-slate-500 uppercase bg-slate-50 border-b border-slate-200">
                            <tr>
                                <th class="px-6 py-4">记录名称</th>
                                <th class="px-6 py-4">类型</th>
                                <th class="px-6 py-4">记录值</th>
                                <th class="px-6 py-4 text-center">状态</th>
                                <th class="px-6 py-4">时间</th>
                                <th class="px-6 py-4 text-right">操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr v-if="records.length === 0" class="border-b border-slate-100">
                                <td colspan="6" class="px-6 py-12 text-center text-slate-400">
                                    <svg class="w-12 h-12 mx-auto mb-3 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 002-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path></svg>
                                    选择域名以查看解析记录，或点击上方同步
                                </td>
                            </tr>
                            <tr v-for="r in records" :key="r.record_id" class="border-b border-slate-100 hover:bg-slate-50 transition">
                                <td class="px-6 py-4 font-bold text-slate-700">{{ r.full_domain }}</td>
                                <td class="px-6 py-4">
                                    <span v-if="r.is_pages" class="bg-purple-100 text-purple-700 px-2.5 py-1 rounded font-bold text-xs">Pages 托管</span>
                                    <span v-else :class="['px-2.5 py-1 rounded font-bold text-xs', r.type==='A'?'bg-emerald-100 text-emerald-700':'bg-blue-100 text-blue-700']">{{ r.type }}</span>
                                </td>
                                <td class="px-6 py-4 font-mono text-slate-600 break-all">{{ r.value }}</td>
                                <td class="px-6 py-4 text-center">
                                    <span v-if="r.status==='PROXIED'" class="bg-orange-100 text-orange-700 px-2.5 py-1 rounded font-bold text-xs">CDN 代理</span>
                                    <span v-else-if="r.status==='DNS_ONLY'" class="bg-slate-100 text-slate-600 px-2.5 py-1 rounded font-bold text-xs">仅 DNS</span>
                                    <span v-else-if="r.status==='ENABLE'" class="bg-emerald-100 text-emerald-700 px-2.5 py-1 rounded font-bold text-xs">已启用</span>
                                    <span v-else class="bg-red-100 text-red-700 px-2.5 py-1 rounded font-bold text-xs">已暂停</span>
                                </td>
                                <td class="px-6 py-4 text-slate-400 text-xs">{{ r.update_time }}</td>
                                <td class="px-6 py-4 text-right space-x-2 whitespace-nowrap">
                                    <a :href="'http://'+r.full_domain" target="_blank" class="inline-block px-3 py-1.5 bg-slate-100 hover:bg-blue-50 hover:text-blue-600 text-slate-600 rounded-md font-bold text-xs transition border border-slate-200">访问</a>
                                    
                                    <template v-if="!r.is_pages">
                                        <button @click="toggleStatus(r)" class="px-3 py-1.5 rounded-md font-bold text-xs transition border border-slate-200"
                                            :class="r.status==='PROXIED'||r.status==='ENABLE' ? 'bg-orange-50 text-orange-600 hover:bg-orange-100' : 'bg-emerald-50 text-emerald-600 hover:bg-emerald-100'"
                                            :disabled="r.type==='TXT' && provider==='cf'">
                                            {{ provider==='cf' ? '开关代理' : (r.status==='ENABLE' ? '暂停' : '启用') }}
                                        </button>
                                        <button @click="confirmDelete(r)" class="px-3 py-1.5 bg-red-50 hover:bg-red-100 text-red-600 border border-red-100 rounded-md font-bold text-xs transition">删除</button>
                                    </template>
                                    <template v-else>
                                        <button disabled class="px-3 py-1.5 bg-slate-50 text-slate-400 border border-slate-200 rounded-md font-bold text-xs cursor-not-allowed" title="Pages 托管记录无法修改状态">代理锁定</button>
                                        <button disabled class="px-3 py-1.5 bg-slate-50 text-slate-400 border border-slate-200 rounded-md font-bold text-xs cursor-not-allowed" title="请前往 CF Pages 控制台解绑">禁删</button>
                                    </template>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="bg-slate-900 rounded-xl shadow-inner p-4 text-xs font-mono overflow-y-auto h-32 border border-slate-800 shrink-0" id="terminal">
                <div v-for="log in logs" class="mb-1">
                    <span class="text-slate-500">[{{ log.time }}]</span>
                    <span :class="log.type==='succ'?'text-emerald-400':'text-red-400'">[{{ log.type==='succ'?'OK':'ERROR' }}]</span>
                    <span class="text-slate-300 ml-1">{{ log.msg }}</span>
                </div>
            </div>
        </template>
    </div>

    <div v-if="showConfig" class="fixed inset-0 bg-slate-900 bg-opacity-50 backdrop-blur-sm flex items-center justify-center z-50">
        <div class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl overflow-hidden border border-slate-200 m-4">
            <div class="p-6 bg-slate-50 border-b border-slate-200 flex justify-between items-center">
                <h3 class="text-lg font-black text-slate-800">云服务商 API 密钥配置</h3>
                <button @click="showConfig = false" class="text-slate-400 hover:text-slate-600 font-bold text-xl">&times;</button>
            </div>
            <div class="p-6 grid grid-cols-1 md:grid-cols-2 gap-6 max-h-[70vh] overflow-y-auto">
                <div class="space-y-3">
                    <h4 class="font-bold text-blue-600 text-sm flex items-center gap-1">🌍 阿里云 (Aliyun)</h4>
                    <input v-model="config.access_key_id" placeholder="AccessKey ID" class="w-full bg-slate-50 border border-slate-300 rounded-lg p-2.5 text-sm">
                    <input v-model="config.access_key_secret" type="password" placeholder="AccessKey Secret" class="w-full bg-slate-50 border border-slate-300 rounded-lg p-2.5 text-sm">
                    <input v-model="config.region_id" placeholder="区域 ID (如 cn-hangzhou)" class="w-full bg-slate-50 border border-slate-300 rounded-lg p-2.5 text-sm">
                </div>
                <div class="space-y-3">
                    <h4 class="font-bold text-cyan-600 text-sm flex items-center gap-1">☁️ 腾讯云 (DNSPod)</h4>
                    <input v-model="config.tc_secret_id" placeholder="SecretId" class="w-full bg-slate-50 border border-slate-300 rounded-lg p-2.5 text-sm">
                    <input v-model="config.tc_secret_key" type="password" placeholder="SecretKey" class="w-full bg-slate-50 border border-slate-300 rounded-lg p-2.5 text-sm">
                </div>
                <div class="space-y-3 md:col-span-2 pt-4 border-t border-slate-100">
                    <h4 class="font-bold text-orange-600 text-sm flex items-center gap-1">🛡️ Cloudflare</h4>
                    <input v-model="config.cf_api_token" type="password" placeholder="API Token (需具备 DNS 编辑权限)" class="w-full bg-slate-50 border border-slate-300 rounded-lg p-2.5 text-sm">
                </div>
            </div>
            <div class="p-6 bg-slate-50 border-t border-slate-200 flex justify-end gap-3">
                <button @click="showConfig = false" class="px-5 py-2.5 rounded-lg font-bold text-slate-600 hover:bg-slate-200 transition">取消</button>
                <button @click="saveConfig" class="px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-bold transition shadow-sm">保存凭据</button>
            </div>
        </div>
    </div>

    <div v-if="showDeleteModal" class="fixed inset-0 bg-slate-900 bg-opacity-50 backdrop-blur-sm flex items-center justify-center z-50">
        <div class="bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden border border-slate-200 m-4">
            <div class="p-6 bg-slate-50 border-b border-slate-200">
                <h3 class="text-lg font-black text-red-600 flex items-center gap-2">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path></svg>
                    危险操作确认
                </h3>
            </div>
            <div class="p-6">
                <p class="text-slate-600 mb-4">您确定要彻底抹除以下解析记录吗？此操作<span class="font-bold text-red-500">不可恢复</span>。</p>
                <p class="font-mono text-sm bg-slate-100 p-3 rounded-lg text-slate-800 break-all">{{ recordToDelete?.full_domain }} <br><span class="text-slate-400">指向:</span> {{ recordToDelete?.value }}</p>
            </div>
            <div class="p-6 bg-slate-50 border-t border-slate-200 flex justify-end gap-3">
                <button @click="showDeleteModal = false" class="px-5 py-2.5 rounded-lg font-bold text-slate-600 hover:bg-slate-200 transition">取消</button>
                <button @click="executeDelete" class="px-5 py-2.5 bg-red-600 hover:bg-red-700 text-white rounded-lg font-bold transition shadow-sm">确认删除</button>
            </div>
        </div>
    </div>

    <div v-if="showAbout" class="fixed inset-0 bg-slate-900 bg-opacity-50 backdrop-blur-sm flex items-center justify-center z-50">
        <div class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl overflow-hidden border border-slate-200 m-4">
            <div class="p-6 bg-slate-50 border-b border-slate-200 flex justify-between items-center">
                <h3 class="text-lg font-black text-slate-800 flex items-center gap-2">ℹ️ 关于 & 帮助</h3>
                <button @click="showAbout = false" class="text-slate-400 hover:text-slate-600 font-bold text-xl">&times;</button>
            </div>
            <div class="p-6 text-sm text-slate-600 space-y-4 max-h-[60vh] overflow-y-auto">
                <h4 class="font-bold text-slate-800 text-base">欢迎使用 CloudDNS Dashboard</h4>
                <p>这是一款基于 Python Flask + Vue3 打造的本地纯净级 Web DNS 管理面板。支持管理阿里云、腾讯云、Cloudflare三大厂商。您的所有密钥凭据均安全保存在本地 <code class="bg-slate-100 px-1 py-0.5 rounded text-slate-800">AccessKey.json</code> 文件中，绝不会上传至任何第三方服务器。</p>
                
                <h4 class="font-bold text-slate-800 mt-4">平台特性说明</h4>
                <ul class="list-disc pl-5 space-y-2">
                    <li><strong>阿里云 / 腾讯云：</strong>支持标准的主机记录解析、启用与暂停操作。修改后，全球生效时间通常在 1-10 分钟。</li>
                    <li><strong>Cloudflare：</strong>无传统的“暂停”概念，按钮对应为“CDN 代理（小黄云）”的开启与关闭。</li>
                    <li><span class="text-purple-600 font-bold">Pages 托管：</span>若您在 Cloudflare Pages 绑定了自定义域名，系统会自动识别并打上 <span class="bg-purple-100 text-purple-700 px-1.5 py-0.5 rounded text-xs">Pages 托管</span> 标签。此类记录受底层保护，<b>无法在面板中修改代理状态或删除</b>，以免您的网页意外掉线。</li>
                </ul>
            </div>
            <div class="p-4 bg-slate-50 border-t border-slate-200 flex justify-between items-center">
                <a href="__GITHUB_REPO_URL__" target="_blank" class="flex items-center gap-2 text-slate-500 hover:text-slate-900 transition font-medium text-sm">
                    <svg class="w-5 h-5 fill-current" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path fill-rule="evenodd" clip-rule="evenodd" d="M12 2C6.477 2 2 6.463 2 11.97c0 4.404 2.865 8.14 6.839 9.458.5.092.682-.216.682-.48 0-.236-.008-.864-.013-1.695-2.782.602-3.369-1.337-3.369-1.337-.454-1.151-1.11-1.458-1.11-1.458-.908-.618.069-.606.069-.606 1.003.07 1.531 1.027 1.531 1.027.892 1.524 2.341 1.084 2.91.828.092-.643.35-1.083.636-1.332-2.22-.251-4.555-1.107-4.555-4.927 0-1.088.39-1.979 1.029-2.675-.103-.252-.446-1.266.098-2.638 0 0 .84-.268 2.75 1.022A9.606 9.606 0 0112 6.82c.85.004 1.705.114 2.504.336 1.909-1.29 2.747-1.022 2.747-1.022.546 1.372.202 2.386.1 2.638.64.696 1.028 1.587 1.028 2.675 0 3.83-2.339 4.673-4.566 4.92.359.307.678.915.678 1.846 0 1.322-.012 2.384-.012 2.712 0 .267.18.577.688.48C19.137 20.107 22 16.373 22 11.969 22 6.463 17.522 2 12 2z"></path></svg>
                    访问 GitHub 开源仓库
                </a>
                <button @click="showAbout = false" class="px-6 py-2 bg-slate-800 hover:bg-slate-900 text-white rounded-lg font-bold transition shadow-sm">关闭</button>
            </div>
        </div>
    </div>

</div>

<script>
    const { createApp, ref, computed, nextTick, onMounted } = Vue;
    createApp({
        setup() {
            const provider = ref('home');
            const showConfig = ref(false);
            const showDeleteModal = ref(false);
            const showAbout = ref(false);
            const recordToDelete = ref(null);
            
            const config = ref({ access_key_id: '', access_key_secret: '', region_id: 'cn-hangzhou', cf_api_token: '', tc_secret_id: '', tc_secret_key: '' });
            const domains = ref([]);
            const zones = ref({});
            const currentDomain = ref('');
            const records = ref([]);
            const logs = ref([]);
            const loading = ref(false);
            const form = ref({ sub: '', type: 'A', val: '' });

            const goHome = () => {
                provider.value = 'home';
                currentDomain.value = '';
                records.value = [];
                clearForm();
            };

            const clearForm = () => { form.value.sub = ''; form.value.val = ''; form.value.type = 'A'; };

            const appendLog = (msg, succ=true) => {
                const now = new Date();
                const time = `${now.getHours().toString().padStart(2,'0')}:${now.getMinutes().toString().padStart(2,'0')}:${now.getSeconds().toString().padStart(2,'0')}`;
                logs.value.push({ time, msg, type: succ ? 'succ' : 'err' });
                if(logs.value.length > 50) logs.value.shift();
                nextTick(() => { const el = document.getElementById('terminal'); if(el) el.scrollTop = el.scrollHeight; });
            };

            const api = async (endpoint, method='GET', body=null) => {
                try {
                    loading.value = true;
                    const res = await fetch(endpoint, {
                        method, headers: {'Content-Type': 'application/json'},
                        body: body ? JSON.stringify(body) : null
                    });
                    const data = await res.json();
                    if(!data.success) throw new Error(data.message);
                    return data;
                } catch(e) {
                    appendLog(e.message, false);
                    throw e;
                } finally { loading.value = false; }
            };

            const loadConfig = async () => {
                try {
                    const data = await api('/api/config');
                    if(data.data) {
                        config.value = {...config.value, ...data.data};
                    }
                } catch(e) {}
            };

            const saveConfig = async () => {
                await api('/api/config', 'POST', config.value);
                appendLog('配置保存成功，如在解析页面请点击同步路由。');
                showConfig.value = false;
                if(provider.value !== 'home') loadDomains();
            };

            const switchProvider = (p) => { provider.value = p; clearForm(); loadDomains(); };

            const loadDomains = async () => {
                domains.value = []; records.value = []; currentDomain.value = '';
                let p_name = provider.value === 'cf' ? 'Cloudflare' : (provider.value === 'tencent' ? '腾讯云' : '阿里云');
                appendLog(`正在同步 ${p_name} 域名列表...`);
                try {
                    const data = await api(`/api/domains?provider=${provider.value}`);
                    domains.value = data.domains; zones.value = data.zones;
                    appendLog(`获取到 ${domains.value.length} 个域名`);
                    if(domains.value.length > 0) {
                        currentDomain.value = domains.value[0];
                        loadRecords();
                    }
                } catch(e) {}
            };

            const loadRecords = async () => {
                clearForm();
                if(!currentDomain.value) return;
                appendLog(`正在加载 ${currentDomain.value} 的解析记录...`);
                try {
                    const zid = zones.value[currentDomain.value] || '';
                    const data = await api(`/api/records?provider=${provider.value}&domain=${currentDomain.value}&zone_id=${zid}`);
                    records.value = data.records;
                    appendLog(`成功加载 ${records.value.length} 条记录`);
                } catch(e) {}
            };

            const addRecord = async () => {
                if(!form.value.val) return alert("请输入记录值");
                try {
                    const payload = {
                        sub: form.value.sub, type: form.value.type, value: form.value.val, 
                        domain: currentDomain.value, provider: provider.value, zone_id: zones.value[currentDomain.value]
                    };
                    const res = await api('/api/records', 'POST', payload);
                    appendLog(res.message);
                    clearForm();
                    loadRecords();
                } catch(e) {}
            };

            const toggleStatus = async (r) => {
                try {
                    let ns = r.provider==='cf' ? !r.proxied : (r.status==='ENABLE'?'DISABLE':'ENABLE');
                    await api('/api/records/status', 'POST', {...r, status: ns});
                    appendLog(`状态已翻转: ${r.full_domain}`);
                    loadRecords();
                } catch(e) {}
            };

            const confirmDelete = (r) => {
                recordToDelete.value = r;
                showDeleteModal.value = true;
            };

            const executeDelete = async () => {
                if(!recordToDelete.value) return;
                try {
                    await api('/api/records', 'DELETE', recordToDelete.value);
                    appendLog(`已删除记录: ${recordToDelete.value.full_domain}`);
                    showDeleteModal.value = false;
                    recordToDelete.value = null;
                    loadRecords();
                } catch(e) {}
            };

            const ipVersionText = computed(() => {
                const v = form.value.val.trim(), t = form.value.type;
                if(t==='TXT'||t==='CNAME') return `输入格式: ${t}`;
                if(!v) return 'IP 版本: 未检测';
                if(/^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/.test(v)) return '检测到: IPv4 (A记录)';
                if(/^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$/.test(v) || v.includes('::')) return '检测到: IPv6 (AAAA记录)';
                return '提示: 无效的 IP 格式';
            });
            const ipVersionColor = computed(() => {
                const text = ipVersionText.value;
                if(text.includes('IPv4')) return 'text-emerald-500';
                if(text.includes('IPv6')) return 'text-blue-500';
                if(text.includes('格式')) return 'text-purple-500';
                if(text.includes('无效')) return 'text-red-500';
                return 'text-slate-400';
            });

            onMounted(() => {
                let isOffline = false;
                setInterval(() => {
                    if(isOffline) return;
                    fetch('/api/ping').catch(() => {
                        isOffline = true;
                        document.getElementById('offline-overlay').style.display = 'flex';
                        try {
                            window.opener = null;
                            window.open('', '_self');
                            window.close();
                            setTimeout(() => { window.location.href = "about:blank"; }, 500);
                        } catch(err) {}
                    });
                }, 2000);
            });

            loadConfig();

            return {
                provider, showConfig, showDeleteModal, showAbout, config, domains, currentDomain, records, logs, form, loading,
                ipVersionText, ipVersionColor, switchProvider, saveConfig, loadDomains, loadRecords, 
                addRecord, toggleStatus, confirmDelete, executeDelete, recordToDelete, goHome
            }
        }
    }).mount('#app')
</script>
</body>
</html>
"""

# ==========================================
# Tkinter 控制台启动器
# ==========================================
def check_latest_version_bg(lbl_cloud):
    def _check():
        try:
            res = requests.get(RELEASES_URL, timeout=5)
            if res.status_code == 200:
                latest = res.json().get('tag_name', '')
                lbl_cloud.after(0, lambda: lbl_cloud.config(text=f"云端版本: {latest}", fg="#10b981"))
            else:
                lbl_cloud.after(0, lambda: lbl_cloud.config(text="云端版本: 获取失败", fg="#ef4444"))
        except:
            lbl_cloud.after(0, lambda: lbl_cloud.config(text="云端版本: 网络异常", fg="#ef4444"))
    threading.Thread(target=_check, daemon=True).start()

def check_for_updates_gui(root):
    def _check():
        try:
            res = requests.get(RELEASES_URL, timeout=5)
            if res.status_code == 200:
                latest_version = res.json().get('tag_name', '')
                curr_match = re.search(r'v(\d+\.\d+\.\d+)', CURRENT_VERSION)
                latest_match = re.search(r'v(\d+\.\d+\.\d+)', latest_version)
                
                is_new = False
                if curr_match and latest_match:
                    curr_v = list(map(int, curr_match.group(1).split('.')))
                    latest_v = list(map(int, latest_match.group(1).split('.')))
                    for l, c in zip(latest_v, curr_v):
                        if l > c: is_new = True; break
                        elif l < c: break
                            
                if is_new:
                    if messagebox.askyesno("发现新版本", f"检测到新版本 {latest_version}，当前版本为 {CURRENT_VERSION}。\n是否前往浏览器下载？", parent=root):
                        webbrowser.open(f"{GITHUB_REPO_URL}/releases")
                else:
                    messagebox.showinfo("检查更新", f"当前已是最新版本: {CURRENT_VERSION}", parent=root)
            else:
                messagebox.showerror("检查更新", f"请求失败，状态码: {res.status_code}", parent=root)
        except Exception as e:
            messagebox.showerror("检查更新", f"检查失败: {str(e)}", parent=root)
            
    threading.Thread(target=_check, daemon=True).start()

def start_tk_launcher():
    root = tk.Tk()
    root.title("CloudDNS Server")
    root.geometry("320x280")
    root.resizable(False, False)
    root.configure(bg="#f8fafc")
    root.eval('tk::PlaceWindow . center')
    
    try:
        icon_png_path = os.path.join(APP_DIR, 'images', 'logo.png')
        icon_ico_path = os.path.join(APP_DIR, 'images', 'logo.ico')
        if os.path.exists(icon_png_path):
            img = tk.PhotoImage(file=icon_png_path)
            root.iconphoto(False, img)
        elif os.path.exists(icon_ico_path):
            root.iconbitmap(icon_ico_path)
    except Exception:
        pass
    
    lbl = tk.Label(root, text="🚀 CloudDNS 服务已启动", font=("Microsoft YaHei", 12, "bold"), bg="#f8fafc", fg="#0f172a")
    lbl.pack(pady=(20, 5))
    
    lbl_local = tk.Label(root, text=f"本地版本: {CURRENT_VERSION}", font=("Microsoft YaHei", 9), bg="#f8fafc", fg="#475569")
    lbl_local.pack()
    
    lbl_cloud = tk.Label(root, text="云端版本: 检查中...", font=("Microsoft YaHei", 9), bg="#f8fafc", fg="#475569")
    lbl_cloud.pack(pady=(0, 10))
    check_latest_version_bg(lbl_cloud)
    
    lbl2 = tk.Label(root, text=f"内网端口: {FLASK_PORT}", font=("Consolas", 9), bg="#f8fafc", fg="#94a3b8")
    lbl2.pack(pady=(0, 15))
    
    def open_browser(): webbrowser.open(f"http://127.0.0.1:{FLASK_PORT}")
    def on_close(): root.destroy(); os._exit(0)
    
    btn_open = tk.Button(root, text="打开 Web 控制台", font=("Microsoft YaHei", 10, "bold"), bg="#3b82f6", fg="white", 
                    activebackground="#2563eb", activeforeground="white", relief="flat", padx=20, pady=5, cursor="hand2", command=open_browser)
    btn_open.pack(pady=(0, 10))
    
    btn_update = tk.Button(root, text="手动检查更新", font=("Microsoft YaHei", 9), bg="#e2e8f0", fg="#475569", 
                    activebackground="#cbd5e1", activeforeground="#475569", relief="flat", padx=15, pady=3, cursor="hand2", command=lambda: check_for_updates_gui(root))
    btn_update.pack()
    
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(1000, open_browser)
    root.mainloop()

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    start_tk_launcher()