# UEMLinux

BlackBerry UEM on Linux — install wizard, profiles, and documentation for tarball-based deployment.

**Remote:** https://github.com/ecoulterbb/UEMLinux

## Workspace layout

This git repo lives inside the broader deployment workspace:

```
C:\Dev\UEMDeploy\
├── UEMLinux\          ← this repo (canonical — work here)
├── config-tools\      ← UEM config-tools Maven project (templates, GCS builders)
├── docs\              ← deployment configuration comparison tables
├── lab\               ← legacy lab copy (use UEMLinux\ instead)
└── scripts\           ← utility scripts
```

## Key files

| File | Purpose |
|------|---------|
| `uem_install.py` | Resumable install wizard |
| `UEM_LINUX_INSTALLATION_GUIDE.md` | End-user installation guide |
| `INSTALL_PACKAGE.md` | Files to ship beside the product tarball |
| `profiles/` | Linux on-prem machine/partition property profiles |
| `reference/` | Archived lab notes and build artifacts |

## Git remote

HTTPS is configured for push (`origin` → `https://github.com/ecoulterbb/UEMLinux.git`).
