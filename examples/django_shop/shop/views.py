"""A tiny flash-sale shop. Only the checkout views are behind the waiting room."""

from __future__ import annotations

import secrets

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from waiting_room.adapters.django import get_room, release_admission

PRODUCT = {"name": "Limited Edition Sneakers", "price": "$180", "stock": 500}


def product(request: HttpRequest) -> HttpResponse:
    """Public product page; browsing is never queued."""
    return render(request, "shop/product.html", {"product": PRODUCT})


@require_http_methods(["GET", "POST"])
def checkout(request: HttpRequest) -> HttpResponse:
    """Protected by the middleware: you only get here once admitted."""
    if request.method == "POST":
        request.session["order_id"] = secrets.token_hex(4).upper()
        return redirect("order_complete")
    stats = get_room("default").stats()
    return render(request, "shop/checkout.html", {"product": PRODUCT, "stats": stats})


def order_complete(request: HttpRequest) -> HttpResponse:
    """Order placed: give the checkout slot back so the next shopper gets in."""
    response = render(
        request,
        "shop/thanks.html",
        {"product": PRODUCT, "order_id": request.session.get("order_id", "?")},
    )
    release_admission(request, response, "default")
    return response
