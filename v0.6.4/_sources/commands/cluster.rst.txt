Cloud Platform
==============
CLI commands for interacting with the Silverback Platform.

.. click:: silverback._cli:login
    :prog: silverback login
    :nested: none

.. click:: silverback._cli:cluster
    :prog: silverback cluster
    :nested: full
    :commands: new, update, list, info, health

.. click:: silverback._cli:workspaces
    :prog: silverback cluster workspaces
    :nested: full
    :commands: new, list, info, update, delete

.. click:: silverback._cli:vars
    :prog: silverback cluster vars
    :nested: full
    :commands: new, list, info, update, remove

.. click:: silverback._cli:registry_auth
    :prog: silverback cluster registry auth
    :nested: full
    :commands: new, list, info, update, remove

.. click:: silverback._cli:bots
    :prog: silverback cluster bots
    :nested: full
    :commands: new, list, info, update, remove, health, start, stop, logs, errors

.. click:: silverback._cli:pay
    :prog: silverback cluster pay
    :nested: full
    :commands: create, add-time, cancel