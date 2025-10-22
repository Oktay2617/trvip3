import re
import sys
import time
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

# Güncel adresi bulmak için kullanılacak portal adresi
PORTAL_DOMAIN = "https://www.selcuksportshd.is/"

# --- YENİ: Global User-Agent (selcuk.py'den kopyalandı) ---
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


# --- GÜNCELLENEN FONKSİYON: GÜNCEL XYZ DOMAIN'İ BULMA ---
def find_working_domain(page):
    """
    Portal sayfasını ziyaret eder ve 'XyzSports Giriş' alt etiketine sahip
    elementin href özelliğinden güncel domain'i çeker.
    """
    print(f"\n🔎 Güncel XyzSports domain'i {PORTAL_DOMAIN} adresinden alınıyor...")
    try:
        page.goto(PORTAL_DOMAIN, timeout=20000, wait_until='domcontentloaded')
        
        # --- KRİTİK DEĞİŞİKLİK ---
        # Artık "XyzSports Giriş" alt etiketine sahip resmi içeren 'a' etiketini arıyoruz
        selector = 'a.site-button:has(img[alt="XyzSports Giriş"])'
        
        page.wait_for_selector(selector, timeout=10000)
        link_element = page.query_selector(selector)
        
        if not link_element:
             print("-> ❌ Portal sayfasında 'XyzSports Giriş' linki bulunamadı.")
             return None
        
        domain = link_element.get_attribute('href')
        
        if not domain:
            print("-> ❌ Link elementinde 'href' özelliği bulunamadı.")
            return None

        # Domain'i temizle (sonundaki '/' karakterini kaldır)
        domain = domain.rstrip('/')
        print(f"✅ Güncel domain başarıyla bulundu: {domain}")
        return domain
        
    except Exception as e:
        print(f"❌ Portal sayfasına ulaşılamadı veya domain alınamadı: {e.__class__.__name__}")
        return None

# --- DEĞİŞİKLİK YOK: KANAL GRUPLAMA MANTIĞI ---
def get_channel_group(channel_name):
    """
    Verilen kanal ismine göre bir grup adı döndürür.
    """
    channel_name_lower = channel_name.lower()
    group_mappings = {
        'BeinSports': ['bein sports', 'beın sports'],
        'S Sports': ['s sport'],
        'Tivibu': ['tivibu spor', 'tivibu'],
        'Ulusal Kanallar': ['a spor', 'trt spor', 'trt 1', 'tv8', 'atv'],
        'Diğer Spor': ['smart spor', 'nba tv', 'eurosport'],
        'Belgesel': ['national geographic', 'nat geo', 'discovery', 'dmax', 'bbc earth', 'history'],
        'Film & Dizi': ['bein series', 'bein movies', 'movie smart']
    }
    for group, keywords in group_mappings.items():
        for keyword in keywords:
            if keyword in channel_name_lower:
                return group
    # Eşleşmezse, "7/24" kanalları için ek kontrol
    if "7/24" in channel_name_lower:
        return "Ulusal Kanallar" # Veya "7/24 Kanallar"
    
    # Kanal adında zaman (örn: 13:30) yoksa, bu da 7/24 kanalı olabilir
    if not re.search(r'\d{2}:\d{2}', channel_name):
         return "7/24 Kanallar" # 'Bein Sports 1' gibi maç dışı yayınlar

    return "Maç Yayınları" # Kalanlar maç yayınıdır

# --- GÜNCELLENDİ: KANAL LİSTESİ KAZIMA (Origin eklendi) ---
def scrape_channel_links(page, domain_to_scrape):
    """
    XyzSports ana sayfasını ziyaret eder ve tüm kanalları
    isim, URL, grup ve GEREKLİ REFERER BİLGİSİ (origin) ile toplar.
    """
    print(f"\n📡 Kanallar {domain_to_scrape} adresinden çekiliyor...")
    channels = []
    try:
        page.goto(domain_to_scrape, timeout=25000, wait_until='domcontentloaded')
        
        link_elements = page.query_selector_all("a[data-url]")
        
        if not link_elements:
            print("❌ Ana sayfada 'data-url' içeren hiçbir kanal linki bulunamadı.")
            return []
            
        for link in link_elements:
            player_url = link.get_attribute('data-url')
            name_element = link.query_selector('div.name')
            
            if name_element and player_url:
                channel_name = name_element.inner_text().strip()
                
                if player_url.startswith('/'):
                    base_domain = domain_to_scrape.rstrip('/')
                    player_url = f"{base_domain}{player_url}"
                
                # --- YENİ: Origin (Referer) bilgisini al (selcuk.py'den kopyalandı) ---
                try:
                    parsed_player_url = urlparse(player_url)
                    player_origin = f"{parsed_player_url.scheme}://{parsed_player_url.netloc}"
                except Exception:
                    player_origin = None 
                
                # Origin alamazsak bu kanalı atla
                if not player_origin:
                    continue 
                # --- BİTTİ ---

                # Kanal adını ve saatini birleştirelim (eğer saat varsa)
                time_element = link.query_selector('time.time')
                if time_element:
                    time_str = time_element.inner_text().strip()
                    if time_str != "7/24":
                        channel_name = f"{channel_name} - {time_str}"
                    else:
                        channel_name = channel_name.replace(time_str, "").strip()

                group_name = get_channel_group(channel_name)
                
                channels.append({
                    'name': channel_name,
                    'url': player_url,
                    'group': group_name,
                    'origin': player_origin # <- YENİ EKLENDİ
                })

        print(f"✅ {len(channels)} adet potansiyel kanal linki bulundu ve gruplandırıldı.")
        return channels
        
    except PlaywrightError as e:
        print(f"❌ XyzSports ana sayfasına ulaşılamadı. Hata: {e.__class__.__name__}")
        return []

# --- DEĞİŞİKLİK YOK: M3U8 ÇIKARMA ---
def extract_m3u8_from_page(page, player_url):
    """
    Oynatıcı sayfasından M3U8 linkini doğrudan oluşturur.
    """
    try:
        page.goto(player_url, timeout=20000, wait_until="domcontentloaded")
        content = page.content()
        base_url_match = re.search(r"this\.baseStreamUrl\s*=\s*['\"](https?://.*?)['\"]", content)
        if not base_url_match:
            print(" -> ❌ 'baseStreamUrl' bulunamadı.", end="")
            return None
        base_url = base_url_match.group(1)
        
        parsed_url = urlparse(player_url)
        query_params = parse_qs(parsed_url.query)
        stream_id = query_params.get('id', [None])[0]
        if not stream_id:
            print(" -> ❌ 'id' parametresi bulunamadı.", end="")
            return None

        m3u8_link = f"{base_url}{stream_id}/playlist.m3u8"
        return m3u8_link

    except Exception:
        print(" -> ❌ Sayfa yüklenirken hata oluştu.", end="")
        return None

# --- GÜNCELLENEN MAIN FONKSİYONU (Başlıklar eklendi) ---
def main():
    with sync_playwright() as p:
        print("🚀 Playwright ile XyzSports M3U8 Kanal İndirici Başlatılıyor...")
        
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
        )
        page = context.new_page()

        # Önce güncel XyzSports domain'ini bul
        xyz_domain = find_working_domain(page)

        if not xyz_domain:
            print("❌ UYARI: Güncel domain portal sayfasından alınamadı. İşlem sonlandırılıyor.")
            browser.close()
            sys.exit(1)

        # Bulunan domain'i kullanarak kanalları kazı
        channels = scrape_channel_links(page, xyz_domain)

        if not channels:
            print("❌ UYARI: Hiçbir kanal bulunamadı, işlem sonlandırılıyor.")
            browser.close()
            sys.exit(1)
        
        m3u_content = []
        # Çıktı dosya adını değiştir
        output_filename = "xyzsports_kanallar.m3u8"
        print(f"\n📺 {len(channels)} kanal için M3U8 linkleri işleniyor...")
        created = 0

        # --- YENİ EKLENEN KISIM: GLOBAL BAŞLIKLARI AYARLA (selcuk.py'den kopyalandı) ---
        # Tüm kanallar aynı kaynağı kullandığı için ilk kanaldan bilgiyi al
        player_origin_host = channels[0]['origin']
        player_referer = player_origin_host + '/' # Sonuna / ekle
        
        m3u_header_lines = [
            "#EXTM3U",
            f"#EXT-X-USER-AGENT:{USER_AGENT}",
            f"#EXT-X-REFERER:{player_referer}",
            f"#EXT-X-ORIGIN:{player_origin_host}"
        ]
        # --- BİTTİ ---
        
        for i, channel_info in enumerate(channels, 1):
            channel_name = channel_info['name']
            player_url = channel_info['url']
            group_name = channel_info['group']
            
            print(f"[{i}/{len(channels)}] {channel_name} (Grup: {group_name}) işleniyor...", end="")
            
            m3u8_link = extract_m3u8_from_page(page, player_url)
            
            if m3u8_link:
                print(" -> ✅ Link bulundu.")
                m3u_content.append(f'#EXTINF:-1 tvg-name="{channel_name}" group-title="{group_name}",{channel_name}')
                m3u_content.append(m3u8_link)
                created += 1
            else:
                print(" -> ❌ Link bulunamadı.")
        
        browser.close()

        if created > 0:
            # --- DEĞİŞTİ: Dosyaya yazma mantığı (selcuk.py'den kopyalandı) ---
            with open(output_filename, "w", encoding="utf-8") as f:
                # Önce global başlıkları yaz
                f.write("\n".join(m3u_header_lines))
                f.write("\n\n") # Kanallardan önce bir boşluk bırak
                # Sonra kanal listesini yaz
                f.write("\n".join(m3u_content))
            print(f"\n\n📂 {created} kanal başarıyla '{output_filename}' dosyasına kaydedildi.")
        else:
            print("\n\nℹ️  Geçerli hiçbir M3U8 linki bulunamadığı için dosya oluşturulmadı.")

        print("\n" + "="*50)
        print("📊 İŞLEM SONUCLARI")
        print("="*50)
        print(f"✅ Başarıyla oluşturulan link: {created}")
        print(f"❌ Başarısız veya atlanan kanal: {len(channels) - created}")
        print("\n🎉 İşlem başarıyla tamamlandı!")

if __name__ == "__main__":
    main()
