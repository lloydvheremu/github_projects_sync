Run the linters with [pre-commit](https://pre-commit.com):

``` sh
pre-commit install
pre-commit run --all-files
```

Run the tests on a throwaway database. They mock GitHub, so they work
offline:

``` sh
odoo-bin -d test_github_sync -i helpdesk_mgmt_github --test-tags /helpdesk_mgmt_github --stop-after-init
```

The same linters and tests run on GitHub Actions for every push and pull
request, in the OCA CI image for Odoo 19.0.
