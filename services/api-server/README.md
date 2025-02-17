# api-server

[![image-size]](https://microbadger.com/images/itisfoundation/api-server. "More on itisfoundation/api-server.:staging-latest image")
[![image-badge]](https://microbadger.com/images/itisfoundation/api-server "More on Public API Server image in registry")
[![image-version]](https://microbadger.com/images/itisfoundation/api-server "More on Public API Server image in registry")
[![image-commit]](https://microbadger.com/images/itisfoundation/api-server "More on Public API Server image in registry")

Platform's public API server

<!-- Add badges urls here-->
[image-size]:https://img.shields.io/microbadger/image-size/itisfoundation/api-server./staging-latest.svg?label=api-server.&style=flat
[image-badge]:https://images.microbadger.com/badges/image/itisfoundation/api-server.svg
[image-version]https://images.microbadger.com/badges/version/itisfoundation/api-server.svg
[image-commit]:https://images.microbadger.com/badges/commit/itisfoundation/api-server.svg
<!------------------------->

## Development

Setup environment

```cmd
make devenv
source .venv/bin/activate
cd services/api-server
make install-dev
```

Then

```cmd
make run-devel
```

will start the api-server in development-mode together with a postgres db initialized with test data. Open the following sites and use the test credentials ``user=key, password=secret`` to manually test the API:

- http://127.0.0.1:8000/docs: redoc documentation
- http://127.0.0.1:8000/dev/docs: swagger type of documentation

## References

- [Design patterns for modern web APIs](https://blog.feathersjs.com/design-patterns-for-modern-web-apis-1f046635215) by D. Luecke
- [API Design Guide](https://cloud.google.com/apis/design/) by Google Cloud

## Clients

- [Python client for osparc-simcore API](https://github.com/ITISFoundation/osparc-simcore-python-client)

## Acknowledgments

  Many of the ideas in this design were taken from the **excellent** work at https://github.com/nsidnev/fastapi-realworld-example-app by *Nik Sidnev* using the **extraordinary** [fastapi](https://fastapi.tiangolo.com/) package by *Sebastian Ramirez*.
