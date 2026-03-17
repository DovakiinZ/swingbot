# Swingbot Deployment Guide - Oracle Cloud Free Tier

This guide walks you through deploying Swingbot on an Oracle Cloud Always Free VM so it runs 24/7 at zero cost.

---

## 1. Create an Oracle Cloud Account

1. Go to [https://cloud.oracle.com](https://cloud.oracle.com) and click **Sign Up**.
2. Enter your name, email, and country. You will need a credit card for verification (you will NOT be charged on the free tier).
3. Choose your **Home Region** (pick one closest to you -- this cannot be changed later).
4. Once your account is created and verified, sign in to the Oracle Cloud Console.

> **Important:** Oracle's Always Free tier includes 1-4 AMD or Arm-based VMs, 24 GB RAM (Arm), and 200 GB block storage -- more than enough for Swingbot.

---

## 2. Create a Free VM Instance

1. In the Oracle Cloud Console, go to **Compute > Instances > Create Instance**.
2. Configure the instance:
   - **Name:** `swingbot`
   - **Image:** Ubuntu 22.04 (or the latest minimal Ubuntu)
   - **Shape:** Click **Change Shape**, select **Ampere** (Arm-based), and choose:
     - 1 OCPU, 6 GB RAM (within free tier)
   - Alternatively, select **AMD** shape `VM.Standard.E2.1.Micro` (1 OCPU, 1 GB RAM -- always free)
3. **Networking:** Use the default VCN or create a new one. Ensure **Assign a public IPv4 address** is selected.
4. **SSH Keys:** Click **Generate a key pair** and download both the private and public key files. Save them securely -- you will need the private key to connect.
5. Click **Create**. Wait for the instance status to show **Running**.
6. Note the **Public IP Address** displayed on the instance details page.

---

## 3. Open Port 8080 (Security List / Firewall)

Oracle Cloud blocks all inbound ports by default. You must open port 8080 for the dashboard.

### In Oracle Cloud Console:

1. Go to **Networking > Virtual Cloud Networks**.
2. Click your VCN, then click the **public subnet**.
3. Click the **Default Security List**.
4. Click **Add Ingress Rules** and enter:
   - **Source CIDR:** `0.0.0.0/0`
   - **Destination Port Range:** `8080`
   - **Protocol:** TCP
   - **Description:** Swingbot Dashboard
5. Click **Add Ingress Rules**.

### On the VM (iptables):

After connecting via SSH (next step), also open the port in the OS firewall:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8080 -j ACCEPT
sudo netfilter-persistent save
```

---

## 4. Deploy Swingbot via SSH

### Connect to your VM:

```bash
# On your local machine (Linux/Mac)
chmod 400 ~/Downloads/ssh-key-*.key
ssh -i ~/Downloads/ssh-key-*.key ubuntu@YOUR_PUBLIC_IP

# On Windows, use PuTTY or Windows Terminal with OpenSSH:
ssh -i C:\Users\YOU\Downloads\ssh-key.key ubuntu@YOUR_PUBLIC_IP
```

### Install Python and dependencies:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3-pip git
```

### Upload and set up the project:

```bash
# Option A: Clone from your repository
git clone https://github.com/YOUR_USER/swingbot.git
cd swingbot

# Option B: Upload via SCP from your local machine
# (run this on your LOCAL machine, not the server)
scp -i ~/Downloads/ssh-key-*.key -r ./swingbot-main ubuntu@YOUR_PUBLIC_IP:~/swingbot
# Then on the server:
cd ~/swingbot
```

### Configure environment:

```bash
# Create your .env file with API keys and credentials
cp .env.example .env   # if an example exists, otherwise create from scratch
nano .env
# Fill in your API keys: ALPACA_API_KEY, ALPACA_SECRET_KEY, etc.
```

### Start the bot:

```bash
chmod +x start.sh stop.sh
./start.sh
```

You should see output like:

```
Swingbot started (PID: 12345)
Dashboard: http://YOUR_PUBLIC_IP:8080
Logs: tail -f logs/output.log
```

### Alternative: Deploy with Docker

If you prefer Docker:

```bash
sudo apt install -y docker.io docker-compose
sudo usermod -aG docker $USER
# Log out and back in for group change to take effect

cd ~/swingbot
docker-compose up -d
```

---

## 5. Access the Dashboard on Your Phone

1. Open a browser on your phone (Chrome, Safari, etc.).
2. Navigate to: `http://YOUR_PUBLIC_IP:8080`
3. Log in with the credentials defined in your `config.yaml`.
4. **Tip:** Add the page to your home screen for app-like access:
   - **iPhone:** Tap the Share button > **Add to Home Screen**
   - **Android:** Tap the three-dot menu > **Add to Home screen**

---

## 6. Auto-Restart with systemd

To ensure Swingbot restarts automatically after a server reboot or crash, create a systemd service.

### Create the service file:

```bash
sudo nano /etc/systemd/system/swingbot.service
```

Paste the following:

```ini
[Unit]
Description=Swingbot Trading Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/swingbot
ExecStart=/usr/bin/python3 run.py --lang en
Restart=always
RestartSec=10
StandardOutput=append:/home/ubuntu/swingbot/logs/output.log
StandardError=append:/home/ubuntu/swingbot/logs/error.log

[Install]
WantedBy=multi-user.target
```

### Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable swingbot
sudo systemctl start swingbot
```

### Useful systemd commands:

```bash
# Check status
sudo systemctl status swingbot

# View live logs
journalctl -u swingbot -f

# Restart after config changes
sudo systemctl restart swingbot

# Stop the bot
sudo systemctl stop swingbot
```

---

## Quick Reference

| Task | Command |
|------|---------|
| Start (manual) | `./start.sh` |
| Stop (manual) | `./stop.sh` |
| Start (Docker) | `docker-compose up -d` |
| Stop (Docker) | `docker-compose down` |
| Start (systemd) | `sudo systemctl start swingbot` |
| Stop (systemd) | `sudo systemctl stop swingbot` |
| View logs | `tail -f logs/output.log` |
| Dashboard | `http://YOUR_IP:8080` |
