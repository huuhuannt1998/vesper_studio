# three.js 0.170.0, vendored for the 3D view (plan W1b)

These files are copied unchanged from the npm package `three@0.170.0` (`npm pack three@0.170.0`), so the page
loads nothing from the internet. `tests/lab/test_ui.py` checks each file against the SHA-256 below.

- three.js and its examples (`build/`, `examples/jsm/loaders`, `environments`, `math`, `utils`): MIT, see `LICENSE`.
- `examples/jsm/libs/ktx-parse.module.js`: ktx-parse by Don McCurdy, MIT.
- `examples/jsm/libs/zstddec.module.js`: zstddec by Don McCurdy, MIT (it embeds Zstandard's decoder, BSD).
- `examples/jsm/libs/basis/`: the Basis Universal transcoder by Binomial LLC, Apache License 2.0.

```
4c40a1ef62450b857c3b2aaf294936304cd552d965fbcd9d32d4c5bcf4ba4454  LICENSE
08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7  build/three.module.min.js
85869bdd22f7cf0b22e68dcefe6cb710480af0f5c901a76f7a7764fd07fc8014  examples/jsm/environments/RoomEnvironment.js
8478b5b6d6b74e7d3082b89f6417321d8d1dc0307f2b30d4484bb11b441696a1  examples/jsm/libs/basis/basis_transcoder.js
6cf17dc889352c42e9acf8897107978d127005fe3386c36a0e3845e27967630a  examples/jsm/libs/basis/basis_transcoder.wasm
34a3a68f08db30a353802c5a762c5dcd89a168a1b56567019ef4a7563ceaf4d4  examples/jsm/libs/ktx-parse.module.js
5cbf818e842628a4464e748594a6deae18ceddda3c2f541e7b3a0ff5fc7611e2  examples/jsm/libs/zstddec.module.js
45139faddd5aaf48ed2d62203d976e5cbd703db1a592de40527f9f6cf58abd44  examples/jsm/loaders/GLTFLoader.js
fbf80e9b0237ffe2e58132d3fcb124a4a62fbd17f9716418ccf865708c2344df  examples/jsm/loaders/KTX2Loader.js
56981604d66cccc26558c710afe08a15177030f8dd3842255d1cc53b4c9b7f3b  examples/jsm/math/ColorSpaces.js
c25b7930e570e9ec56173cd3b866ec8d2e10016630db3937efb439daf1cedbf6  examples/jsm/utils/BufferGeometryUtils.js
ee412be1aa05f3051c2bda8ea89612c1ba70c5c87de62b56040282120d5eb991  examples/jsm/utils/SkeletonUtils.js
94ff7b608caf3b827ffa95bd6092b9d174732b856915b38ed006b4f049f95675  examples/jsm/utils/WorkerPool.js
```
