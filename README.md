# CloudDNS Dashboard

![Version](https://img.shields.io/badge/version-v1.0.0-blue)
![Python](https://img.shields.io/badge/python-3.8%2B-green)
![License](https://img.shields.io/badge/license-MIT-orange)

CloudDNS 是一款基于 **Python Flask** + **Vue3** + **Tailwind CSS** 打造的轻量级本地化 Web DNS 管理面板。它旨在为开发者提供一个统一、纯净的界面，用于高效管理多家云服务商的解析记录。

---

## 📸 界面预览

![CloudDNS 主界面预览](https://github.com/QsSama-W/CloudDNS/blob/main/ScreenShot_image.png)

---

## 🌟 核心特性

- **多厂商支持**：一站式管理 **阿里云 (Aliyun)**、**腾讯云 (DNSPod)** 以及 **Cloudflare**。
- **本地化存储**：所有 API 密钥均保存在本地 `AccessKey.json` 中，绝不上传第三方服务器，确保凭据安全。
- **时区自动转换**：针对 Cloudflare API 自动处理 UTC 到 UTC+8（北京时间）的转换。
- **智能识别**：
  - 自动识别并标记 Cloudflare Pages 托管记录，防止误删。
  - 实时检测输入值的 IP 类型（IPv4/IPv6）。
- **极简操作**：支持一键开关 Cloudflare CDN 代理（小黄云）、快速暂停/启用解析记录。
- **响应式设计**：内置实时操作日志终端，调试过程一目了然。
- **双模启动**：内置 Tkinter 图形启动器，一键开启服务并自动跳转浏览器。

---

## 🚀 快速开始

### 1. 克隆仓库
```bash
git clone [https://github.com/QsSama-W/CloudDNS.git](https://github.com/QsSama-W/CloudDNS.git)
cd aliddns
```

### 2. 安装依赖
项目依赖各云厂商的官方 SDK 及 Flask 后端框架：
```bash
pip install requests flask aliyun-python-sdk-core aliyun-python-sdk-alidns tencentcloud-sdk-python
```

### 3. 运行程序
直接运行主脚本即可启动控制台：
```bash
python CloudDNS.py
```

### 4. 或者直接使用已打包完成的程序启动
点击这里下载已打包程序[CloudDNS](https://github.com/QsSama-W/CloudDNS/releases/tag/v1.0.0)

---

## ⚙️ 配置说明

启动程序后，点击左侧菜单栏的 **“⚙️ API 密钥配置”** 即可在网页端直接录入：

- **阿里云**：需准备 `AccessKey ID` 和 `AccessKey Secret`。
- **腾讯云**：需准备 `SecretId` 和 `SecretKey`。
- **Cloudflare**：需准备具有 DNS 编辑权限的 `API Token`。

> **安全提示**：配置文件 `AccessKey.json` 会生成在程序同级目录下。**请务必将其添加到 `.gitignore` 中**，切勿将其上传至公开仓库。

---

## 🛠️ 技术栈

- **后端 (Backend)**: Python 3, Flask
- **前端 (Frontend)**: Vue 3 (Composition API), Tailwind CSS
- **图形界面 (GUI)**: Tkinter (Server Launcher)
- **云端接入 (SDKs)**: Aliyun SDK, Tencent Cloud SDK, Cloudflare API v4

---

## 📄 开源协议

本项目基于 [MIT License](LICENSE) 协议开源。

---
**QsSama-W** - [GitHub Profile](https://github.com/QsSama-W)
