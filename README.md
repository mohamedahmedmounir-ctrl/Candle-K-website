# 🕯️ Candle K — E-Commerce Website

A complete luxury candle e-commerce website built with Python/Flask.

## Quick Start

```bash
cd candle_k
pip install flask werkzeug
python app.py
```

Visit: http://localhost:5000
Admin:  http://localhost:5000/admin/login

**Admin login:** the username is `admin`. Set the `ADMIN_PASSWORD` environment variable before the first run to choose the password. If it isn't set, a random password is generated and printed once in the server log. There is no shared default password. You can change it any time in Admin → Change Password.

---

## ✅ Features

### Storefront
- Home page with hero, featured candles, testimonials
- Shop page with category filters, search, sort
- Product detail page with image gallery
- Cart with quantity management
- **Enhanced checkout** with promo code support
- Instapay payment with screenshot upload (drag & drop)
- Cash on Delivery option
- Public order tracking page (`/track/<order_number>`)

### Automatic Emails
Every customer email is sent automatically — no manual action needed.

| Trigger | Email Sent |
|---------|-----------|
| Order placed | Order confirmation |
| Admin marks Paid | Payment confirmed |
| Admin marks Processing | Being prepared |
| Admin marks Shipped | Shipped + tracking number |
| Admin marks Out for Delivery | Arriving today |
| Admin marks Delivered | Delivered! |
| Admin marks Cancelled | Cancellation notice |

Emails include: order summary, item list, totals, delivery address, timeline, tracking number, and a "Track My Order" button.

### Admin Dashboard
- **Products** — add, edit, delete, upload multiple images
- **Orders** — view all orders, update status, add tracking numbers
- **Promo Codes** — create % / fixed / free-shipping codes
- **Content** — edit every word on the website
- **Testimonials** — manage customer reviews
- **Categories** — manage product categories
- **Email Log** — see every email sent (success/failure)

---

## 📧 Setting Up Real Emails (Gmail)

1. Enable 2-Step Verification on your Gmail account
2. Go to: Google Account → Security → App Passwords
3. Create an App Password for "Mail"
4. In Admin → Site Content → Email Settings, fill in:
   - **email_host**: `smtp.gmail.com`
   - **email_port**: `587`
   - **email_user**: `youraddress@gmail.com`
   - **email_password**: the 16-character App Password
   - **email_enabled**: `true`
5. Set **store_url** to your real domain (for tracking links in emails)

---

## 🎁 Default Promo Codes

| Code | Discount |
|------|---------|
| `CANDLE20` | 20% off |
| `WELCOME10` | 10% off |
| `SAVE50` | 50 EGP off (min 300 EGP) |
| `FREESHIP` | Free shipping (min 200 EGP) |

---

## Project Structure

```
candle_k/
├── app.py                    # Main Flask application
├── candle_k.db               # SQLite database (auto-created)
├── requirements.txt
├── README.md
├── templates/
│   ├── base.html             # Shared layout
│   ├── index.html            # Homepage
│   ├── shop.html             # Product listing
│   ├── product.html          # Product detail
│   ├── cart.html             # Shopping cart
│   ├── checkout.html         # Checkout with promo codes
│   ├── order_success.html    # Order confirmation
│   ├── track.html            # Public order tracking
│   └── admin/
│       ├── base.html         # Admin layout
│       ├── login.html        # Admin login (rate-limited)
│       ├── dashboard.html    # Analytics overview
│       ├── orders.html       # Order management
│       ├── order_detail.html # Order + status update
│       ├── products.html     # Product list
│       ├── product_form.html # Add/edit product
│       ├── promos.html       # Promo code manager
│       ├── content.html      # Site content + email settings
│       ├── email_log.html    # Email send history
│       ├── testimonials.html
│       └── categories.html
├── static/
│   ├── css/main.css          # Storefront styles
│   ├── css/admin.css         # Admin styles
│   ├── js/main.js            # Interactive JS (cart, upload, parallax)
│   ├── js/admin.js           # Admin JS
│   └── images/logo.jpeg
└── uploads/
    ├── products/             # Product images
    └── payments/             # Instapay screenshots
```

---

## Deployment Notes

- Set `app.secret_key` to a long random string in production
- Set `store_url` in Site Content to your real domain
- For production use gunicorn: `gunicorn app:app`
