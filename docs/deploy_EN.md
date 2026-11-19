# Deploy on Oracle Cloud

A concise guide for running the FastAPI web app, background worker, SQLite database, and daily cleanup on one Oracle Cloud Linux VM.

All domains, addresses, repository URLs, and key names below are placeholders. Replace them before running commands. Never commit credentials, SSH private keys, uploaded photos, or production databases.

## Architecture

```text
Browser → [optional Cloudflare proxy] → Nginx HTTPS → FastAPI :8000
                                                        ↕
                                              SQLite + private files
                                                        ↕
                                                      Worker
Daily systemd timer → Cleanup CLI
```

| Resource | Location |
| --- | --- |
| Application and virtual environment | `/opt/rental-reports`, `.venv` |
| Managed Python | `/opt/rental-python` |
| Environment variables | `/opt/rental-reports/.env` |
| Persistent database and files | `/var/lib/rental-reports` |
| Application user | `rental` |

The boot volume is persistent across reboots. Source updates do not delete the separate data directory. Volume deletion and application cleanup can delete data; maintain independent backups.

## 1. Create the VM and network

Choose an Always Free-eligible shape in your home region, such as `VM.Standard.A1.Flex`, and keep CPU, memory, and total boot/block storage within your account's current free allowance. Do not assume the default shape is eligible. Trial credits are temporary; Always Free allowances are separate. Verify current eligibility and actual costs in the console.

Use an Ubuntu platform image, on-demand capacity, and default boot-volume performance. This deployment used Ubuntu 26.04 ARM64 with a separately installed Python 3.12. Shielded and confidential-computing options were left off.

If necessary, create a VCN with the **Internet Connectivity** wizard. Select its **public subnet** for the VM and enable public IPv4 assignment. Download the SSH private key.

Add these stateful ingress rules to the VM's subnet Security List or attached NSG:

| Source | Protocol | Source port | Destination port |
| --- | --- | --- | --- |
| Administrator's public IP `/32` | TCP | All | 22 |
| `0.0.0.0/0` | TCP | All | 80 |
| `0.0.0.0/0` | TCP | All | 443 |

Leave **Stateless unchecked**. Do not expose port 8000.

## 2. Connect from macOS

Store the downloaded private key under `~/.ssh/` and restrict permissions:

```sh
chmod 700 ~/.ssh
chmod 600 ~/.ssh/oracle-app.key
```

Add to `~/.ssh/config`, preserving existing entries:

```sshconfig
Host oracle-app
    HostName <PUBLIC_IP>
    User ubuntu
    IdentityFile ~/.ssh/oracle-app.key
    IdentitiesOnly yes
```

```sh
ssh oracle-app
```

Use the full key path; a bare filename is relative to the current directory.

## 3. Install the application

Run on the VM. Replace `<REPOSITORY_URL>` with your repository URL; private repositories require authentication, preferably a read-only deploy key.

```sh
sudo apt update
sudo apt install -y git python3-venv python3-pip nginx certbot python3-certbot-nginx
sudo install -d -o ubuntu -g ubuntu /opt/rental-reports
git clone <REPOSITORY_URL> /opt/rental-reports
cd /opt/rental-reports
```

### Install Python outside the home directory

The pinned HEIF package failed to compile under Python 3.14 ARM64. Use Python 3.12 with prebuilt HEIF wheels. Install Python under `/opt`, because the systemd units use `ProtectHome=true`.

For a fresh installation:

```sh
python3 -m venv .uv-bootstrap
.uv-bootstrap/bin/python -m pip install uv
sudo install -d -o ubuntu -g ubuntu -m 755 /opt/rental-python
export UV_PYTHON_INSTALL_DIR=/opt/rental-python
export UV_PYTHON_BIN_DIR=/opt/rental-python/bin
.uv-bootstrap/bin/python -m uv python install 3.12
```

Find the installed interpreter and create the environment:

```sh
.uv-bootstrap/bin/python -m uv python find --managed-python 3.12
```

Use the resulting `/opt/rental-python/.../bin/python3.12` path below:

```sh
.uv-bootstrap/bin/python -m uv venv --python <PYTHON_PATH> --seed .venv
.venv/bin/python -m pip install --only-binary=pillow-heif -r requirements.lock.txt
.venv/bin/python -m pip check
readlink -f .venv/bin/python
```

The final interpreter must resolve outside `/home`. Recreate virtual environments at their intended path rather than moving them. If `.venv` already exists, stop services and preserve it before replacement.

## 4. Configure credentials and persistent storage

```sh
id rental >/dev/null 2>&1 || sudo useradd --system --user-group \
  --home-dir /opt/rental-reports --shell /usr/sbin/nologin rental
sudo install -d -o rental -g rental -m 700 /var/lib/rental-reports
test -f .env || cp .env.example .env
nano .env
```

Set these values, plus optional SMTP settings:

```dotenv
MUSE_API_KEY=<SECRET>
MUSE_MODEL=<MODEL_NAME>
PUBLIC_BASE_URL=https://reports.example.com
DATA_DIR=/var/lib/rental-reports
MIN_FREE_DISK_MB=512
```

```sh
sudo chown root:rental .env
sudo chmod 640 .env
sudo -u rental .venv/bin/python -c "import fastapi, pillow_heif; print('Imports OK')"
```

`PUBLIC_BASE_URL` is the browser-facing origin used for links and origin checks. The Nginx upstream remains localhost. Leaving `DATA_DIR=./data` causes a read-only-filesystem error under the supplied service restrictions.

## 5. Start web and worker services

```sh
sudo cp deploy/rental-web.service deploy/rental-worker.service \
  deploy/rental-cleanup.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rental-web rental-worker
systemctl status rental-web rental-worker --no-pager
curl -I http://127.0.0.1:8000/
```

Inspect failures with:

```sh
sudo journalctl -u rental-web -u rental-worker --since "5 minutes ago" --no-pager
```

## 6. Allow HTTP and HTTPS through the host firewall

UFW being absent does not mean there are no firewall rules. Inspect before changing anything:

```sh
sudo iptables -L INPUT -n -v --line-numbers
sudo nft list ruleset
```

If INPUT ends with a blanket REJECT, insert an allowance **before that rule**. Replace `<REJECT_RULE_NUMBER>` with the current number; do not blindly reuse an old number.

```sh
sudo iptables -I INPUT <REJECT_RULE_NUMBER> -p tcp -m conntrack --ctstate NEW \
  -m multiport --dports 80,443 -j ACCEPT
sudo apt install -y iptables-persistent
sudo netfilter-persistent save
```

Preserve SSH and Oracle instance-service rules. Do not flush the firewall. With iptables-nft, iptables and nft may show the same rules, not two independent firewalls.

## 7. Configure DNS, Nginx, and HTTPS

Point `reports.example.com` to the VM's public IPv4. Start with Cloudflare **DNS only** if using Cloudflare. Do not publish an origin AAAA record unless origin IPv6 works.

Create `/etc/nginx/sites-available/rental-reports`:

```nginx
server {
    listen 80;
    server_name reports.example.com;
    access_log off;
    client_max_body_size 15m;
    client_body_timeout 60s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 120s;
    }
}
```

```sh
sudo ln -sfn /etc/nginx/sites-available/rental-reports /etc/nginx/sites-enabled/rental-reports
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

From your **local Mac**, confirm external HTTP connectivity before requesting a certificate:

```sh
curl -I --connect-timeout 10 http://reports.example.com/
```

On the VM:

```sh
sudo certbot --nginx -d reports.example.com --redirect
sudo nginx -t
sudo certbot renew --dry-run
systemctl list-timers --all | grep -i certbot
```

Certbot installs the certificate, updates Nginx, and schedules renewal. **Do not overwrite its configuration with the initial HTTP example.** Keep DNS and the validation path reachable for renewals.

### Optional Cloudflare proxy

Universal SSL covers browser-to-Cloudflare traffic; Nginx still needs its own certificate. After origin HTTPS works, enable the proxy.

To avoid changing other subdomains, leave the zone-wide SSL mode unchanged and create a **Configuration Rule**:

```text
(http.host eq "reports.example.com")
```

Under **SSL → Add**, select **Full (strict)** and deploy. A rule with no selected setting can fail with `action parameters are required`. Review overlapping rules. DNS-only records are unaffected by proxy SSL settings.

A Cloudflare 522 indicates origin connection failure. If HTTP also fails with DNS-only, check OCI ingress, host firewall, and Nginx before retrying Certbot.

## 8. Schedule daily cleanup

The repository timer runs daily at **03:00 America/Toronto**, with up to 30 seconds of randomized delay. Change its timezone/time if needed.

```sh
sudo cp deploy/rental-cleanup.service deploy/rental-cleanup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rental-cleanup.timer
systemctl list-timers rental-cleanup.timer
```

`Persistent=true` catches a missed run after downtime. Test manually only when ready to delete expired data:

```sh
sudo systemctl start rental-cleanup.service
sudo journalctl -u rental-cleanup.service -n 20 --no-pager
sudo cat /var/lib/rental-reports/cleanup-status.json
```

Confirm zero failures. An inactive oneshot service after successful execution is normal. Links expire immediately; physical deletion may wait until the next daily run.

## 9. Apply future Git updates

Commit and push reviewed changes from your development machine. Then on the VM:

```sh
cd /opt/rental-reports
git status --short
git branch --show-current
git rev-parse HEAD
git fetch origin
git log --oneline HEAD..@{upstream}
```

Record the current commit. Resolve unexpected server changes or branch divergence before deploying. Wait for active jobs when possible; interrupted work may retry AI calls or email delivery.

For a short maintenance deployment:

```sh
sudo systemctl stop rental-web rental-worker
sudo systemctl stop rental-cleanup.timer rental-cleanup.service
git pull --ff-only
.venv/bin/python -m pip install --only-binary=pillow-heif -r requirements.lock.txt
.venv/bin/python -m pip check
```

If a step fails, investigate before restarting. Do not use destructive Git resets to hide conflicts.

- **Unit/timer changes:** copy changed files to `/etc/systemd/system/`, then `sudo systemctl daemon-reload`.
- **Environment changes:** edit the server `.env`; Git does not deploy secrets.
- **Nginx changes:** merge into the live configuration, preserve Certbot settings, run `nginx -t`, then reload.
- **Database changes:** take a consistent backup and plan migration/rollback separately.

After all steps succeed:

```sh
sudo systemctl start rental-web rental-worker
sudo systemctl start rental-cleanup.timer
systemctl is-active nginx rental-web rental-worker rental-cleanup.timer
curl -I https://reports.example.com/
sudo journalctl -u rental-web -u rental-worker --since "5 minutes ago" --no-pager
```

`--ff-only` refuses an automatic merge when histories diverge. Rolling back code does not automatically restore dependencies or a migrated database.

## Final checks

- Upload photos, complete real analysis, and download PDF/ZIP files.
- Verify email delivery if enabled.
- Confirm cleanup schedule, certificate renewal, and recovery after a planned reboot.
- Back up SQLite consistently using its backup API or while writers are stopped; back up private files too. Align backup retention with deletion policy.

## References

- [Oracle Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [uv storage locations](https://docs.astral.sh/uv/reference/storage/)
- [Cloudflare Configuration Rules](https://developers.cloudflare.com/rules/configuration-rules/)
