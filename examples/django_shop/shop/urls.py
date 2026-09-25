from django.contrib import admin
from django.urls import include, path

from shop import views

urlpatterns = [
    path("admin/", admin.site.urls),
    # Waiting page, status polling, admit callback, health.
    path("", include("waiting_room.adapters.django.urls")),
    path("", views.product, name="product"),
    # Everything under /checkout/ is protected by WAITING_ROOM["PROTECT"].
    path("checkout/", views.checkout, name="checkout"),
    path("checkout/done/", views.order_complete, name="order_complete"),
]
