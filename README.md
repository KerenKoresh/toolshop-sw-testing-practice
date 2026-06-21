# ToolShop

חנות כלים פשוטה בהשראת practicesoftwaretesting.com — עם API ציבורי ואתר עם תיבת חיפוש.

## מה יש כאן

- **Backend**: Flask + SQLAlchemy — Postgres בענן (`DATABASE_URL`), SQLite מקומית כברירת מחדל
- **עיצוב**: תבנית [Dopetrope](https://html5up.net) של HTML5 UP (נכסי התבנית ב-`static/theme/`)
- **בלי משתמשים**: הקטלוג ציבורי לצפייה. עריכה/מחיקה מוגבלת לבעל ה-**edit-token** של הפריט
- **האתר לצפייה בלבד**: עיון, חיפוש, וצפייה בפריט. **כל פעולות הכתיבה (יצירה/עדכון/מחיקה) דרך ה-API בלבד** (ראי `/docs`)
- **דף תיעוד Swagger** (`/docs`): תיעוד אינטראקטיבי עם "Try it out"
- **CORS פתוח** — ה-API נגיש מכל מקור

## איך עובדת הבעלות (edit-token)

אין הרשמה ואין login. כשיוצרים מוצר דרך ה-API, התשובה כוללת **פעם אחת** שדה `edit_token` (מחרוזת סודית). רק ה-hash שלו נשמר ב-DB. כדי לעדכן או למחוק את אותו מוצר חייבים לשלוח את ה-token ב-header:
`X-Edit-Token: <token>`

שמרי את ה-token שקיבלת ביצירה — בלעדיו אי אפשר לערוך או למחוק את הפריט. מוצרי הקטלוג הבסיסי (seed) אינם ניתנים לעריכה (אין להם token).

## עמודי האתר

| עמוד | כתובת |
|------|-------|
| חנות + חיפוש | `/` |
| פרטי מוצר | `/product/<id>` |
| תיעוד API (Swagger) | `/docs` |

## הרצה מקומית

```bash
cd toolshop
pip install -r requirements.txt
python app.py
# פתחי http://localhost:5000
```

## מיגרציה / אתחול DB

הסקריפט יוצר את הסכמה וזורע את הקטלוג הבסיסי אם הוא ריק. עובד מול ה-DB שב-`DATABASE_URL`:

```bash
python scripts/migrate.py            # יצירת טבלאות + seed אם ריק (idempotent)
python scripts/migrate.py --status   # הצגת מצב נוכחי בלבד
python scripts/migrate.py --reset    # מחיקת כל הטבלאות, יצירה מחדש וזריעה
```

הריצי פעם אחת אחרי הקמת Postgres חדש. (האפליקציה גם מאתחלת אוטומטית בעלייה, אז זה בעיקר ל-reset/בקרה.)

## API

בסיס: `/api` — קריאה ויצירה פתוחות לכולם. עדכון ומחיקה דורשים `X-Edit-Token`.

| Method | Endpoint | Auth | תיאור |
|--------|----------|------|-------|
| GET | `/api/products` | — | כל המוצרים |
| GET | `/api/products?search=plier` | — | חיפוש לפי שם חלקי (case-insensitive) |
| GET | `/api/products?id=3` | — | מוצר לפי ID מדויק (רשימה) |
| GET | `/api/products/3` | — | מוצר בודד לפי ID |
| POST | `/api/products` | — | יצירת מוצר (מחזיר `edit_token`) |
| PUT/PATCH | `/api/products/3` | `X-Edit-Token` | עדכון מוצר |
| DELETE | `/api/products/3` | `X-Edit-Token` | מחיקת מוצר |
| GET | `/api/health` | — | בדיקת בריאות |

### דוגמאות

```bash
# יצירה — שמרי את ה-edit_token מהתשובה
curl -X POST http://localhost:5000/api/products \
  -H "Content-Type: application/json" \
  -d '{"name":"Rubber Mallet","price":9.9,"category":"Hammer"}'
# -> {"id": 13, ..., "edit_token": "AbC123..."}

# עדכון (עם ה-token)
curl -X PUT http://localhost:5000/api/products/13 \
  -H "Content-Type: application/json" \
  -H "X-Edit-Token: AbC123..." \
  -d '{"price":19.99,"in_stock":false}'

# מחיקה (עם ה-token)
curl -X DELETE http://localhost:5000/api/products/13 \
  -H "X-Edit-Token: AbC123..."

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
  "in_stock": true,
  "editable": false
}
```

## פריסה לרשת (זמין לכולם)

האתר מוכן לפריסה. שתי אופציות חינמיות מומלצות:

### אפשרות א' — Render עם Postgres (מומלץ, נתונים נשמרים)

הקובץ `render.yaml` כבר מגדיר גם **Postgres** וגם את ה-Web Service, ומחבר ביניהם אוטומטית דרך `DATABASE_URL`.

1. העלי את התיקייה ל-GitHub repo.
2. ב-[render.com](https://render.com) → **New → Blueprint** → חברי את ה-repo.
3. Render יקרא את `render.yaml`, ייצור את ה-Postgres ואת השירות, ויזריק את `DATABASE_URL`.
4. Deploy. תקבלי כתובת ציבורית כמו `https://toolshop.onrender.com`. הנתונים נשמרים בין deploys.

> בלי `DATABASE_URL` האפליקציה נופלת חזרה ל-SQLite מקומי (טוב לפיתוח). אפשר לראות איזה DB פעיל ב-`/api/health`.

### אפשרות ב' — Railway

1. [railway.app](https://railway.app) → New Project → Deploy from GitHub.
2. Railway יזהה את ה-`Procfile` (`web: gunicorn app:app`).
3. תחת Settings → Networking → Generate Domain כדי לקבל כתובת ציבורית.

### Docker (לכל ספק שתומך)

```bash
docker build -t toolshop .
docker run -p 8080:8080 toolshop
```

> הערה: עם Postgres הנתונים נשמרים בין deploys (בניגוד ל-SQLite על דיסק זמני).
> ה-Postgres החינמי של Render מוגבל בזמן — שדרגי לתוכנית בתשלום לשימוש ארוך טווח.
