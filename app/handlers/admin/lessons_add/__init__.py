from aiogram import Router

from .choose_type import router as choose_type_router
from .single import router as single_router
from .rule import router as rule_router
from .nav import router as nav_router

router = Router()
router.include_router(choose_type_router)
router.include_router(single_router)
router.include_router(rule_router)
router.include_router(nav_router)
