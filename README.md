# Minecraft Multi-PC Server Launcher

Launcher Python para usar el mismo servidor Minecraft desde varios PCs con Google Drive Desktop.

La forma recomendada es sencilla: en Drive hay una carpeta con ZIPs fechados, y cada PC trabaja en una copia local. Al iniciar, la app descomprime el ZIP mas nuevo. Al parar, crea un ZIP nuevo con fecha y conserva solo los 3 ZIPs mas recientes.

## Flujo

```text
Google Drive/Minecraft/
  server-20260709-180000.zip
  server-20260709-203000.zip
  .minecraft_multi_pc_state/

run
  -> lock compartido
  -> extrae ZIP mas nuevo a carpeta local limpia
  -> prepara variables ServerPackCreator
  -> ejecuta start.ps1

parar
  -> envia stop
  -> si el script pide confirmacion, envia s
  -> crea server-YYYYMMDD-HHMMSS.zip
  -> borra ZIPs antiguos hasta dejar solo los 3 ultimos
  -> libera lock
```

## Uso rapido

1. Prepara un servidor funcional en una carpeta local.
2. Acepta `eula.txt`, genera `world`, `libraries`, `versions`, `server.properties` y prueba que `start.ps1` arranca.
3. Comprime el contenido del servidor en un ZIP inicial y ponlo en la carpeta de Drive.
4. Copia `config.example.toml` a `config.toml`.
5. Ejecuta la GUI:

```powershell
python -m minecraft_multi_pc_server.gui
```

O crea el EXE:

```powershell
.\scripts\build-gui-exe.ps1
```

Salida:

```text
dist/MinecraftServerLauncher.exe
```

El build deja tambien el instalador de dependencias en la misma carpeta:

```text
dist/InstalarDependencias.exe
```

Ejecutalo una vez en cada PC. Comprueba e instala Tailscale y Google Drive Desktop mediante `winget`; despues debes abrir ambas aplicaciones e iniciar sesion. El launcher es autocontenido, por lo que el usuario final no necesita Python.

La alternativa desde PowerShell es:

```powershell
.\scripts\install-dependencies.ps1
```

Para preparar tambien un entorno de desarrollo con el proyecto editable y PyInstaller:

```powershell
.\scripts\install-dependencies.ps1 -Development
```

## Configuracion

Ejemplo recomendado:

```toml
remote_archive_dir = "C:/minecraft-multi-pc-server/minecraft-servers"
local_server_dir = "C:/minecraft-multi-pc-server/server_temp"
start_command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "start.ps1"]
machine_name = "Rafa"
connection_mode = "tailscale"
server_ip = "blank"

sync_ignore = [".tmp.drivedownload/**", ".tmp.driveupload/**", "logs/**", ".mixin.out/**"]
stale_lock_minutes = 30
heartbeat_seconds = 30
archive_compression_level = 1
```

Campos:

- `remote_archive_dir`: carpeta de Google Drive con los ZIPs fechados. Es el modo recomendado.
- `local_server_dir`: carpeta local temporal donde se descomprime y ejecuta el servidor.
- `start_command`: comando de arranque. Para ServerPackCreator en GUI usa `["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "start.ps1"]`.
- `machine_name`: nombre visible en el lock.
- `connection_mode`: `tailscale` muestra la IP VPN para jugar; `manual` deja la conexion a tu cargo.
- `server_ip`: ajuste local para `server.properties`. Con Tailscale usa `"blank"` para dejar `server-ip=` en blanco. `"auto"` detecta la IPv4 local, y una IP manual fuerza esa IP.
- `state_dir`: opcional. Si no se pone, usa `remote_archive_dir/.minecraft_multi_pc_state`.
- `sync_ignore`: archivos que no entran en los ZIPs nuevos.
- `stale_lock_minutes`: minutos para considerar antiguo un lock sin heartbeat.
- `heartbeat_seconds`: frecuencia del heartbeat mientras corre el servidor.
- `archive_compression_level`: `0` sin compresion, `1` rapido, `9` mas pequeno y lento.

No ignores `world`, `mods`, `config`, `libraries`, `versions`, `.fabric`, `server.properties`, `eula.txt`, `variables.txt` ni `start.bat`.

## GUI

La GUI permite:

- Elegir `config.toml`.
- Recordar el ultimo TOML usado en `.minecraft_launcher_gui_state.json`.
- Ver carpeta Drive y carpeta local en pantalla principal.
- Ver la IP local detectada antes de arrancar.
- Ver estado Tailscale y la direccion para jugar.
- Abrir Google Drive Desktop al entrar y al salir para comprobar que todo esta sincronizado.
- Elegir script `.bat`, `.cmd` o `.ps1`.
- Editar rutas, nombre de PC, `server-ip` y config completa desde `Mas ajustes`.
- Iniciar servidor.
- Parar servidor de forma segura.
- Recuperar una subida fallida.
- Quitar lock manualmente.

### Checkpoint de Google Drive

Al abrir la GUI, el launcher intenta abrir Google Drive Desktop. Si no encuentra la app, abre la carpeta configurada en `remote_archive_dir` como fallback. Despues muestra una ventana de `Continuar`. Espera a que Google Drive Desktop indique que la carpeta esta sincronizada antes de continuar.

Al salir pasa lo mismo: se abre solo Google Drive Desktop, o la carpeta Drive si la app no esta disponible, para que compruebes que el ZIP nuevo ya termino de subirse. Al pulsar `Continuar`, el launcher intenta cerrar la ventana que haya abierto. No mata el proceso de Google Drive, porque cortar el cliente de sincronizacion podria dejar una subida incompleta.

El launcher intenta poner la ventana de Google Drive por encima del launcher, y la ventana de `Continuar` por encima de Google Drive para que puedas revisar el estado y continuar sin usar Alt+Tab.

El boton `Actualizar estado` solo relee el `config.toml`, el lock compartido y el ZIP activo. No descarga, no sube y no modifica archivos.

## Conexion con Tailscale

Tailscale evita abrir puertos del router. Cada jugador debe instalar Tailscale, iniciar sesion y estar invitado a la misma red privada. El launcher no instala Tailscale ni gestiona invitaciones; solo detecta si hay una IP Tailscale activa.

Config recomendada:

```toml
connection_mode = "tailscale"
server_ip = "blank"
```

Cuando Tailscale esta activo, la GUI muestra una direccion como:

```text
100.99.98.97:25565
```

Esa es la direccion que deben usar los jugadores. El launcher exige que `tailscale status` indique `BackendState: Running`; una IP guardada en el adaptador no basta. Si la GUI muestra `No activo`, bloquea el boton de inicio.

El boton `Conectar VPN` abre el cliente de Tailscale, ejecuta `tailscale up` de forma silenciosa y espera una IP activa. Cuando conecta, el launcher actualiza el estado y habilita `Iniciar servidor`. Si la cuenta requiere iniciar sesion, completa ese paso en la ventana de Tailscale y vuelve a pulsar el boton. El launcher no almacena credenciales ni puede aceptar invitaciones por el usuario.

Con Tailscale, `server-ip=` se deja vacio en `server.properties`. Asi Minecraft escucha en local, LAN y VPN, que es lo mas robusto para cambiar de PC.

## ServerPackCreator

Para packs ServerPackCreator, la app prepara automáticamente antes de arrancar:

- Usa `start.ps1` desde la GUI, o `start.bat` si lo arrancas manualmente.
- `WAIT_FOR_USER_INPUT=false`
- `RESTART=false`
- Si `connection_mode = "tailscale"`, deja `server.properties` con `server-ip=` justo antes de abrir el servidor.
- Cierra desde la GUI con `Parar servidor` o escribe `stop` en consola.
- Espera a que Google Drive termine de sincronizar antes de abrir en otro PC.

## ZIPs y backups

El primer ZIP debe existir antes del primer `run`; la app no crea un servidor vacio por seguridad.

Si hay varios ZIPs, elige el mas nuevo por fecha en el nombre:

- `server-20260709-180000.zip`
- `server_20260709_180000.zip`
- `server-2026-07-09-18-00-00.zip`

Si ningun nombre tiene fecha valida, usa la fecha de modificacion del archivo.

Despues de crear un ZIP nuevo, la app limpia la carpeta Drive y deja solo los 3 ZIPs mas recientes. Si Google Drive esta tocando un ZIP antiguo y Windows no deja borrarlo, la app muestra un warning en logs pero no bloquea la subida correcta.

Antes de extraer, el launcher valida que el ZIP no este vacio, no contenga rutas peligrosas, enlaces simbolicos, nombres duplicados ni mas datos de los que caben en disco. La extraccion se hace en una carpeta temporal. La copia local anterior solo se cambia al final y se restaura si ese reemplazo falla.

Al subir, el ZIP se escribe primero como `.tmp`, se vuelve a leer para comprobar su integridad y despues se renombra. Nunca se publica deliberadamente un ZIP vacio. La limpieza de backups protege siempre el ZIP que acaba de crearse, incluso si otro archivo tiene una fecha futura en su nombre.

## Lock y recuperacion

La carpeta estado guarda `lock.json`, `last_run.json` y `last_error.json`. Debe vivir en Drive para que todos los PCs vean si alguien esta usando el servidor.

Cada lock nuevo guarda el nombre del PC anfitrion y su direccion de juego. Si intentas iniciar mientras otro PC tiene el servidor abierto, el launcher bloquea el arranque y muestra a quien pertenece y una direccion como `100.88.77.66:25565`. Los locks creados por versiones antiguas no contienen esa direccion; se mostraran como lock antiguo hasta que se complete una ejecucion con la version nueva.

Si la subida falla al cerrar, el lock queda como `upload_failed`. En ese caso usa `Recuperar subida` desde el mismo PC para crear el ZIP nuevo y liberar el lock.

La recuperacion se rechaza desde otro nombre de maquina: su carpeta local puede contener un mundo distinto. Si se pierde el heartbeat mientras el servidor esta abierto, tampoco se sube automaticamente; se conserva la copia local y se deja el lock para recuperarla con seguridad.

Usa `Quitar lock` solo si sabes que ningun PC sigue ejecutando el servidor.

## Protecciones de arranque

Antes de ejecutar el script, el launcher comprueba de nuevo Tailscale, valida que el ejecutable y el script configurados existen y revisa el puerto de `server.properties`. Si el puerto ya esta ocupado, bloquea el arranque porque puede quedar otro servidor Java abierto en ese PC.

Las rutas local, remota y de estado no pueden solaparse. El heartbeat debe ser menor que el tiempo que convierte el lock en antiguo. Las escrituras de configuracion, estado, `variables.txt` y `server.properties` son atomicas para no dejar archivos a medias si Windows o Drive interrumpen una operacion.

La parada automatica sigue la secuencia `stop`, `s`, `Ctrl+C`, `s`. Si el proceso no responde, no se mata ni se comprime el mundo mientras sigue abierto: el launcher avisa y vuelve a habilitar el reintento.

## CLI

La CLI sigue disponible:

```powershell
minecraft-launcher -c config.toml status
minecraft-launcher -c config.toml run
minecraft-launcher -c config.toml sync-up
minecraft-launcher -c config.toml unlock --force
```

## Desarrollo

Tests:

```powershell
python -m unittest discover -s tests -v
```

Build del EXE:

```powershell
.\scripts\build-gui-exe.ps1
```

Se generan juntos `dist/MinecraftServerLauncher.exe` y `dist/InstalarDependencias.exe`.
El script ejecuta ambos binarios en modo smoke test y falla si no pueden inicializarse.

La version Qt/C++ se descarta en este repo porque complicaba demasiado el build. La version actual es Python + Tkinter + PyInstaller.
