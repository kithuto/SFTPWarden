# Providers

| Provider | Read users | Mutate users | Notes |
| --- | ---: | ---: | --- |
| YAML | yes | yes | Default provider |
| CSV | yes | yes | Public keys are newline-separated |
| MySQL | planned | no | Mutations require a write strategy |
| PostgreSQL | planned | no | Mutations require a write strategy |

SQL mutations fail clearly until a write strategy is defined.

