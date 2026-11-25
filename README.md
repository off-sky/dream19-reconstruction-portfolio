# DREAM19: data-driven selection of bridge reconstruction portfolios

**Version 1.0.0 · 25 November 2026**

This research release accompanies the economics dissertation on algorithms for economic recovery decisions based on large databases. It contains the frozen model, source-linked datasets, reproducible calculations and supplementary experiments for Chapter 3. The investment portfolio study covers 19 bridge projects, seven budget levels and 24 evaluated network configurations. Estimated benefits are conditional model outputs, not observed national traffic volumes or investment recommendations.

## Reproduction

From the repository directory:

```sh
python3 -m pip install -r requirements.txt
bash run.sh
```

After dependencies are installed, the calculations run offline without OSRM or access to the private repository. The script verifies the SHA-256 manifest and reconstructs the national gravity weights, traffic-counter diagnostic scaling, portfolio comparisons and monetary scenarios, the six-bridge NSZU example, and the VIIRS quality and portfolio experiments. It archives the calculated tables, figures and logs. See `DATA_DICTIONARY.md` and `RELEASE_VERIFICATION.md` for details. The repository supports reproduction from frozen inputs and routing caches; it is not a complete rebuild of the original road graphs from raw spatial datasets.

## Data availability

The repository includes the research inputs derived from publicly accessible sources and the model-generated datasets used in the open calculations. `SOURCES_AND_LICENSES.csv` identifies their provenance and attribution. Original source terms remain applicable.

**TomTom:** the original observations, derived numerical comparison data and associated outputs are not included in this repository. Reproduction of the TomTom comparison in the second circuit, including the VIIRS external benchmark, requires the historical data and replay module held in the private research repository. These materials are available from the author on reasonable request, subject to the provider's licensing terms and permitted redistribution. Where redistribution is prohibited, access is arranged under the provider's terms. No substitute or imputed TomTom observations are supplied. All open calculations run without TomTom.

## Licence and citation

The author's original program code is licensed under MIT (see `LICENSE`). This licence does not extend to third-party software, source datasets, or data derived from those datasets where other conditions apply. See `SOURCES_AND_LICENSES.csv` for source links and attribution, including © OpenStreetMap contributors and ODbL.

Cite this software using `CITATION.cff` and cite the individual source datasets listed in `SOURCES_AND_LICENSES.csv`. Cite the version actually used.

[Українська документація](README_UA.md)
# dream19-reconstruction-portfolio
# dream19-reconstruction-portfolio
