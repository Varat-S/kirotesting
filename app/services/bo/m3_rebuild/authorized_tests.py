"""Historical two-request harness: unsupported after shared BO integration."""


def run_authorized_batch(*args, **kwargs):
    raise ValueError(
        'The private two-request harness is retired. Use m3_rebuild.credit_test '
        'for an explicitly authorized single request through normal BO preparation.'
    )
