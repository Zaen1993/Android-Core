# -*- coding: utf-8 -*-
from media_utils import BaseGalleryBrowser


class G(BaseGalleryBrowser):
    """ماسح الوسائط - يرث جميع الوظائف من BaseGalleryBrowser."""
    __slots__ = ()

    def __init__(self, sc=None, tg=None):
        super().__init__(sc, tg)


def create(sc=None, tg=None):
    return G(sc, tg)
