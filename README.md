# ToolShop

חנות כלים פשוטה בהשראת practicesoftwaretesting.com — עם API ציבורי ואתר עם תיבת חיפוש.

## מה יש כאן

- **Backend**: Flask + SQLite (CRUD מלא + חיפוש)
- **אימות JWT**: הרשמה / כניסה; כל משתמש רואה ומנהל **רק את המוצרים שלו** (פרטי לחלוטין)
- **Frontend**: דף בית עם תיבת חיפוש; לחיצה על מוצר פותחת טאב חדש עם הפרטים; הודעת שגיאה כשאין תוצאה
- **דף ניהול** (`/admin`): טופס להוספה / עריכה / מחיקה של מוצרים דרך הממשק
- **דף תיעוד Swagger** (`/docs`): תיעוד אינטראקטיבי + כפתור Authorize ל-JWT
- **CORS פתוח** — ה-API נגיש מכל מקור

## אימות (JWT)

נרשמים או מתחברים, מקבלים `token`, ושולחים אותו בכל בקשה למוצרים:
`Authorization: Bearer <token>`

| Method | Endpoint | תיאור |
|--------|----------|-------|
| POST | `/api/register` | יצירת משתמש חדש (מחזיר token) |
| POST | `/api/login` | כניסה (מחזיר token) |
| GET | `/api/me` | פרטי המשתמש המחובר |

```bash
# הרשמה
curl -X POST http://localhost:5000/api/register \
  -H "Content-Type: application/json" \
  -d '{"email":"me@example.com","password":"secret1"}'

# שימוש ב-token שהתקבל
TOKEN=...   # מתוך התשובה
curl http://localhost:5000/api/products -H "Authorization: Bearer $TOKEN"
```

> ⚠️ בפריסה חובה להגדיר `SECRET_KEY` כ-env var קבוע (חתימת ה-JWT). אחרת כל deploy מנתק את כולם.

## עמודי האתר

| עמוד | כתובת |
|------|-------|
| כניסה / הרשמה | `/login` |
| חנות + חיפוש | `/` |
| פרטי מוצר | `/product/<id>` |
| ניהול מוצרים | `/admin` |
| תיעוד API (Swagger) | `/docs` |

## הרצה מקומית

```bash
cd toolshop
pip install -r requirements.txt
python app.py
# פתחי http://localhost:5000
```

## API (מוצרים)

בסיס: `/api` — **כל ה-endpoints של המוצרים דורשים `Authorization: Bearer <token>`** ופועלים רק על המוצרים של המשתמש המחובר.

| Method | Endpoint | תיאור |
|--------|----------|-------|
| GET | `/api/products` | כל המוצרים |
| GET | `/api/products?search=plier` | חיפוש לפי שם חלקי (case-insensitive) |
| GET | `/api/products?id=3` | מוצר לפי ID מדויק (רשימה) |
| GET | `/api/products/3` | מוצר בודד לפי ID |
| POST | `/api/products` | יצירת מוצר |
| PUT/PATCH | `/api/products/3` | עדכון מוצר |
| DELETE | `/api/products/3` | מחיקת מוצר |
| GET | `/api/health` | בדיקת בריאות |

### דוגמאות

```bash
# יצירה
curl -X POST http://localhost:5000/api/products \
  -H "Content-Type: application/json" \
  -d '{"name":"Rubber Mallet","price":9.9,"category":"Hammer","description":"Soft mallet"}'

# עדכון
curl -X PUT http://localhost:5000/api/products/1 \
  -H "Content-Type: application/json" \
  -d '{"price":19.99,"in_stock":false}'

# מחיקה
curl -X DELETE http://localhost:5000/api/products/1

# חיפוש לפי שם חלקי
curl "http://localhost:5000/api/products?search=plier"

# לפי ID מדויק
curl "http://localhost:5000/api/products?id=3"
```

מבנה מוצר:
```json
{
  "id": 1,
  "name": "Combination Pliers",
  "description": "...",
  "price": 14.15,
  "category": "Pliers",
  "in_stock": true
}
```

## פריסה לרשת (זמין לכולם)

האתר מוכן לפריסה. שתי אופציות חינמיות מומלצות:

### אפשרות א' — Render (הכי פשוט)

1. העלי את התיקייה ל-GitHub repo.
2. היכנסי ל-[render.com](https://render.com) → New → Web Service → חברי את ה-repo.
3. Render יזהה את `render.yaml` אוטומטית. אם לא:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT`
4. Deploy. תקבלי כתובת ציבורית כמו `https://toolshop.onrender.com`.

### אפשרות ב' — Railway

1. [railway.app](https://railway.app) → New Project → Deploy from GitHub.
2. Railway יזהה את ה-`Procfile` (`web: gunicorn app:app`).
3. תחת Settings → Networking → Generate Domain כדי לקבל כתובת ציבורית.

### Docker (לכל ספק שתומך)

```bash
docker build -t toolshop .
docker run -p 8080:8080 toolshop
```

> הערה: ב-Render בתוכנית החינמית מערכת הקבצים זמנית, כך שה-SQLite מתאפס בכל deploy.
> לדמו זה מצוין. לנתונים קבועים — חברי Postgres או דיסק קבוע (paid).
