# Architecture

The current implementation keeps modules mostly flat while the public API stabilizes:

| Concern | Current module |
| --- | --- |
| Project config | `sftpwarden.config` |
| Global config | `sftpwarden.global_config` |
| Contexts | `sftpwarden.contexts` |
| Providers and users | `sftpwarden.providers` |
| Runtime apply/state | `sftpwarden.runtime` |
| Remote SSH checks | `sftpwarden.remote_checks` |
| Watcher | `sftpwarden.watcher` |
| Compose rendering | `sftpwarden.compose` |
| Password hashing | `sftpwarden.passwords` |

This keeps imports stable while leaving room to split modules into packages later.

