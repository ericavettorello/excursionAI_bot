from aiogram import Router

from excursion_ai.handlers import (
    about,
    admin,
    admin_schedule,
    apply,
    catalog,
    consult,
    fallback,
    my_applications,
    myid,
    start,
)

router = Router(name="root")
# Меню и /start раньше формы и консультации.
router.include_router(start.router)
router.include_router(myid.router)
router.include_router(catalog.router)
router.include_router(my_applications.router)
router.include_router(about.router)
router.include_router(admin.router)
router.include_router(admin_schedule.router)
router.include_router(consult.router)
router.include_router(apply.router)
router.include_router(fallback.router)
