# Prompt 2 · Establish the local Vue–FastAPI connection

~~~text
Connect the local Vue development server to the Python backend. The check for
this task is that a request sent through the frontend server reaches FastAPI
and returns the same JSON as a direct request to the backend.

CHECK
Read README.md and AGENTS.md. Inspect backend/, frontend/, the dependency
manifests and lockfiles. Identify the Python executable, available FastAPI and
ASGI server, Node.js, and the frontend package manager. Reuse the working
Python environment. Do not create a virtual environment by default.

TAKE ACTION
For any missing tool or dependency, show the compatible version, exact command,
target environment, and files affected. Wait for my approval before installing
software or changing dependency declarations or lockfiles. Use a minimal Vue
setup. In backend/, expose GET /api/health with a small JSON status response.
Configure the frontend development server to forward /api requests to FastAPI.

VERIFY
Start both services on localhost. Request /api/health at the backend address,
then at the frontend address. Record both URLs, status codes, and response
bodies. Stop the backend and repeat the second request to check that it really
depended on FastAPI. Restart the backend and confirm recovery. Add the tested
start and stop commands to README.md.
~~~
