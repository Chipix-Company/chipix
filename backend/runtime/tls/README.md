# TLS Fronting Template (E-04)

This directory provides a production-oriented reverse-proxy TLS pattern for ChipVerify runtime node deployments.

## Goals

- Expose only HTTPS to client laptops.
- Keep backend bound to loopback (`127.0.0.1`) behind the proxy.
- Enforce certificate-based transport encryption for multi-host deployments.

## Files

- `nginx-chipverify.conf.example`: baseline Nginx reverse-proxy TLS config.

## Deployment Flow (Nginx)

1. Keep backend on loopback:
   - `CHIPVERIFY_BACKEND_HOST=127.0.0.1`
  - `CHIPVERIFY_BACKEND_PORT=7348`
2. Copy `nginx-chipverify.conf.example` to your Nginx sites config.
3. Replace `chipverify.example.com` and certificate paths.
4. Validate config:
   - `sudo nginx -t`
5. Reload Nginx:
   - `sudo systemctl reload nginx`

## Certificate Management Guidance

Use one of the two approved patterns below.

### Pattern A: Enterprise Internal PKI

- Request a server certificate for the runtime DNS name.
- Install keypair at controlled paths such as:
  - `/etc/ssl/chipverify/fullchain.pem`
  - `/etc/ssl/chipverify/privkey.pem`
- Apply strict permissions:
  - private key readable by root/Nginx service account only.
- Track certificate expiry in monitoring and renew before expiration.

### Pattern B: Let's Encrypt (Internet-reachable DNS)

- Install `certbot` and Nginx plugin.
- Issue certificate:
  - `sudo certbot --nginx -d chipverify.example.com`
- Confirm automatic renewal timer/service is active:
  - `systemctl status certbot.timer`
- Test renewal:
  - `sudo certbot renew --dry-run`

## End-to-End HTTPS Validation

Run the validation script after the proxy is live:

```bash
python backend/runtime/validate_tls_endpoint.py --base-url https://chipverify.example.com --require-hsts
```

Pass criteria:

- HTTPS request to `/api/v1/health` returns HTTP 200.
- JSON body includes `{"status": "ok"}`.
- HSTS header is present when `--require-hsts` is used.

## Security Notes

- Do not expose backend directly on public interfaces unless explicitly required and controlled.
- Keep `CHIPVERIFY_ALLOWED_ORIGINS` aligned with the HTTPS hostname(s).
- Never commit certificates or private keys to source control.
