---
name: Bug report
about: Something doesn't work
---

**Describe the bug**

**Environment**
- OS / kernel:
- `smartctl --version`:
- Disks (`lsblk -d -o NAME,MODEL,ROTA`):

**Logs**

journalctl -u hdd-health-predictor.service -n 100 --no-pager
