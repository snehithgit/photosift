# PhotoSift on ZimaOS

GitHub Actions tests every push to `main`, then publishes `ghcr.io/snehithgit/photosift:latest`
for x86-64. The image holds the API, the web app and the worker. The GPU laptop still runs the
model service and agent, and pulls work from Zima.

## Install

1. In [docker-compose.yml](docker-compose.yml), change `/DATA/Gallery` in **both** services to your
   photo folder. Leave `:ro` on it, so originals can't be written to from the container.
2. In ZimaOS, open App Store, choose Custom Install, and import that file.
3. Set `PHOTOSIFT_AGENT_TOKEN` to a long random secret.
4. Open `http://<zima-address>:8000` and choose `/library`, or a folder inside it, from the list.
5. When you're ready, click **Scan now** under Library. Nothing is read until you do.

To start over, use Settings, Libraries, Clear database. It keeps your originals, exported copies,
libraries, settings, prompts, Teach labels and model setup.

App data lives in `/DATA/AppData/photosift`. Keep it on a local disk: SQLite must not run on a
network share. The app has no login, so keep port 8000 on your home network and off the internet.

## Connect the laptop

The laptop needs no file share. For each job it downloads that one original from Zima, using the
same token, and checks its hash. In `agent.local.json` on the laptop:

- set `serverUrl` to `http://<zima-address>:<port>`
- put the same secret in a local file and point `tokenFile` at it
- leave `rootMappings` empty

The token and photos travel over plain HTTP, so keep this on your trusted home network.
`--init-local` is only for a server on the same machine, so don't use it here. The rest of the
setup is in the [agent guide](../../agent/README.md).

Start the laptop with **Start** in the PhotoSift Agent window (see the
[laptop guide](../../laptop/README.md)). Then, in the web app, click **Turn on models** under
Settings, Laptop agent. A database with no model setup prepares nothing until you do. Later, click
**Encode prompts** there.

## Prepare on Zima

Preparing needs no GPU, and the photos are already on Zima. Click **Prepare on Zima** under
Settings, Laptop agent, and the worker prepares them with direct read-only access to the library.
The laptop then does only the embed and assess stages. Photos prepared on the laptop before the
switch are prepared again, because the contract includes the machine's Python and platform; D3's
answers and your decisions are kept.

It uses up to 3 threads, at low priority, and always leaves a core free. Set
`PHOTOSIFT_PREPARE_THREADS` on the `worker` service to change that. To go back to the laptop, run
`--configure-preparation` from the [agent guide](../../agent/README.md). No change to this compose
file is needed: the worker already mounts the library. Speed on a real ZimaOS machine isn't measured
yet.

## Not tested yet

CI runs the tests on Linux and smoke-tests the image, but this has not run on a real ZimaOS machine.
Folder permissions, downloading photos from a real Zima, and speed on Zima's disks and CPU are
unverified.
