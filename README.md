# CodeDetector

Kod değişikliklerini inceleyerek olası regresyonları ve değişikliğe özel test adımlarını çıkaran web uygulaması. Eski ve yeni kodu, Git diff çıktısını veya kaynak dosyalarını karşılaştırır; bulguları ilgili dosya ve satırlarla birlikte gösterir.

## Özellikler

- Eski/yeni kod, unified diff ve dosya yükleme ile analiz.
- Satır numaralarıyla eklenen ve silinen kodları gösterme.
- Bulguları önem, kanıt türü ve güven düzeyine göre sınıflandırma.
- Modelin verdiği dosya, satır ve kod alıntılarını gerçek diff ile karşılaştırma.
- Bulgudan ilgili diff satırına geçiş.
- Öncelik, kategori ve duruma göre filtrelenebilen test kontrol listesi.
- Testleri bekliyor, geçti, kaldı veya engellendi olarak işaretleme.
- Analiz raporunu ve test durumlarını Markdown veya JSON olarak indirme.
- Model gerektirmeyen, açıkça etiketlenmiş örnek demo.

## Nasıl çalışır?

Flask eski ve yeni kodun farkını hesaplar veya Git diff metnini ayrıştırır. Analiz, Ollama üzerinden Gemma 4 modeline gönderilir. Modelin döndürdüğü yapılandırılmış sonuç kontrol edilir; diff ile doğrulanamayan satır ve alıntı referansları temizlenir veya uyarıyla gösterilir.

Arayüz HTML, CSS ve JavaScript ile hazırlanmıştır. Frontend derlemesi ve Node.js kurulumu gerekmez. Yüklenen kod çalıştırılmaz; üretilen testler de uygulama tarafından otomatik yürütülmez. Kontrol listesindeki sonuçları kullanıcı işaretler.

## Kurulum

Python 3.10 veya üzeri gerekir. Canlı analiz için ayrıca [Ollama](https://ollama.com) ve bir Gemma 4 modeli kurulmalıdır.

### Windows

İlk kurulumda `setup.bat`, sonraki çalıştırmalarda `run.bat` dosyasını kullanın. Kurucu Python ortamını ve bağımlılıkları hazırlar; eksik araçları ve modeli kurmadan önce onay ister. `setup.bat /y` soruları otomatik onaylar. `setup.bat --check` model kurulumunu atlayarak ortam hazırlığını ve testleri çalıştırır.

Elle kurulum:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe app.py
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python app.py
```

Uygulamayı <http://127.0.0.1:5000> adresinde açın.

### Yerel model

Ollama çalışır durumdayken varsayılan modeli indirin:

```bash
ollama pull gemma4:e2b
ollama list
```

Farklı bir Gemma 4 etiketi kullanacaksanız `.env` içindeki `GEMMA_MODEL` değerini değiştirin. Uygulama modelin kurulu olduğunu ve Gemma 4 ailesine ait olduğunu kontrol eder. Başka model ailesine otomatik geçiş yapılmaz. Modelin bellek ihtiyacı ve analiz süresi donanıma, bağlam boyutuna ve model etiketine bağlıdır.

### Demo

**Örnek senaryoyu yükle** düğmesiyle kayıt doğrulama fonksiyonunun iki sürümünü açın. Model erişilemiyorsa sunulan **DEMO sonucunu göster** seçeneğiyle hazır bulguları inceleyin. Model açıkken demoyu denemek için `.env` dosyasında `DEMO_MODE=on` ayarlayın.

Demo yalnızca uygulamanın örnek senaryosunu kabul eder; kendi kodunuz için hazır sonuç üretmez.

## Yapılandırma

Ayarlar `.env` dosyasından veya ortam değişkenlerinden okunur. Tüm seçenekler [.env.example](.env.example) dosyasındadır.

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama sunucusu |
| `GEMMA_MODEL` | `gemma4:e2b` | Gemma 4 etiketi |
| `GEMMA_TIMEOUT` | `300` | Model yanıtı zaman aşımı, saniye |
| `GEMMA_NUM_CTX` | `16384` | Bağlam boyutu |
| `GEMMA_NUM_PREDICT` | `4096` | Üretilecek en fazla token sayısı |
| `GEMMA_MAX_RETRIES` | `1` | Geçersiz JSON çıktısında yeniden deneme sayısı |
| `ANALYSIS_LANGUAGE` | `tr` | Analiz dili: `tr` veya `en` |
| `DEMO_MODE` | `auto` | `auto`, `on` veya `off` |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `5000` | Uygulama adresi ve portu |

## Veri akışı ve sınırlar

Kod ve analiz raporu uygulama tarafından kalıcı olarak saklanmaz. Arayüzdeki rapor ve işaretlemeler sayfa belleğinde tutulur; saklamak için dışa aktarın. Normal analiz kayıtları kodun kendisi yerine dosya sayısı, değişiklik boyutu ve süre bilgisini içerir.

Varsayılan bağlantıda analiz yerel Ollama sunucusuna gider. `OLLAMA_URL` başka bir sunucuya ayarlanırsa analiz için hazırlanan kod da o adrese gönderilir. Uygulama yerel kullanım için hazırlanmıştır.

Varsayılan sınırlar: kod alanı başına 200.000 karakter, yüklenen dosya başına 512 KB, HTTP isteği başına 2 MB ve model girdisi için 24.000 karakterlik diff. Büyük değişiklikleri dosya veya işlev bazında bölmek gerekebilir.

Satır ve alıntı doğrulaması, modelin risk yorumunun doğru olduğunu garanti etmez. Eksik bağlam, çağıran kodlar veya çalışma ortamı bilinmediğinde bulgular ayrıca incelenmelidir. Uygulama kod çalıştırmaz, test sonucu üretmez ve birleştirme onayı vermez.

## Testler

Bağımlılıkların kurulu olduğu Python ortamında:

```bash
python -m pytest -q
```

Testler diff ayrıştırmayı, dil ve dosya denetimini, model ailesi kontrolünü, kanıt doğrulamasını, API hatalarını ve örnek demo akışını kapsar. Model yanıtları testlerde taklit edilir; bu kontroller canlı model kalitesini ölçmez.

## Dizinler

- `app.py`: Flask uygulaması ve API rotaları.
- `codedetector/`: diff ayrıştırma, model bağlantısı, analiz doğrulama ve demo.
- `templates/`: ana arayüz.
- `static/`: JavaScript, CSS ve ikon.
- `tests/`: birim ve API testleri.
- `setup.bat` / `run.bat`: Windows kurulum ve başlatma betikleri.

Projenin kaynak kodu için henüz bir lisans dosyası bulunmuyor. Kullanılan modelin lisans koşulları ayrıca geçerlidir.
