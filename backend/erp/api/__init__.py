def register_blueprints(app):
    from . import accounting, admin, auth, data, file_objects, hr, inventory, ops, reports, trade
    for module in (auth, admin, inventory, trade, accounting, hr, ops, reports, data, file_objects):
        module.register(app)
