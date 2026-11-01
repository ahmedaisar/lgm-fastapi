"""Shared offline fixtures — minimal HTML replicating the unihotel.org
structures the parsers expect (search fragments, hotel booking page, SPO
tables). Kept in one module so parser tests and API tests stay consistent.
"""

SEARCH_FRAGMENT_HTML = """
<input class="part-number" value="1">
<input class="parts-total" value="3">
<tr class="hotel-list-item-wrapper" data-hotel-id="9001">
  <td>1</td>
  <td><img src="x.jpg"></td>
  <td>Maldives, North Male Atoll 5*</td>
  <td><a class="hotel-card__title-link" href="/hotel/anantara-dhigu/">Anantara Dhigu</a></td>
  <td class="hotel-item-price-td" data-price="1200.50">1,200.50</td>
  <td>On request</td>
  <td class="hotel-item-price-currency-td">$</td>
  <td><span class="qq-btn-place" data-value='{"roomType":"Deluxe Beach Villa","boardType":"BB","region":"South Male Atoll"}'></span></td>
</tr>
<tr class="hotel-list-item-wrapper">
  <td>2</td>
  <td><img src="y.jpg"></td>
  <td>Maldives, Rasdhoo</td>
  <td><a class="hotel-card__title-link" href="/hotel/kuramathi-maldives/">Kuramathi Maldives 4*</a></td>
  <td data-price="850">850</td>
  <td>available</td>
  <td class="hotel-item-price-currency-td">$</td>
  <td><span class="qq-btn-place" data-value='{"roomType":"Standard Room","boardType":"HB","region":"Rasdhoo"}'></span></td>
</tr>
"""

HOTEL_PAGE_HTML = """
<html><body>
<div id="hotel-rooms-wrapper"><h1>Anantara Dhigu Maldives Resort</h1></div>
<input id="api-request-hotel-id" value="9001">
<input name="csrfmiddlewaretoken" value="page-csrf-123">
<div id="rooms-wrapper">
  <div class="row">
    <div class="room-title">Beach Villa</div>
    <ul class="hotel-room-features-wrapper"><li>Wi-Fi</li><li>Outdoor pool</li></ul>
    <div class="meal-wrapper"><div class="hotel-item-price-description-title">Breakfast (BB)</div></div>
    <div class="calculation-wrapper">
      <span class="hidden price">1200.00</span>
      <span class="hidden supplier-company">S77</span>
      <span class="hidden calculation-method">Per night</span>
      <div class="hotel-item-price-currency">$</div>
    </div>
  </div>
  <div class="row">
    <div class="room-title">Water Villa</div>
    <div class="meal-wrapper"><div class="hotel-item-price-description-title">Half board (HB)</div></div>
    <div class="calculation-wrapper"><span class="hidden price"></span></div>
  </div>
</div>
</body></html>
"""

SPO_HTML = """
<table class="table-striped">
  <h4>Early booking <span class="text-success">-20%</span> <small>SPO</small> ( Code: EB21 )</h4>
  <tr><td>Accommodation:</td><td>01.11.2026 - 20.12.2026</td></tr>
  <tr><td>Booking period:</td><td>01.06.2026 - 31.08.2026</td></tr>
  <tr><td>Rooms:</td><td>Beach Villa: 20%, Water Villa: 20%</td></tr>
  <tr><td>Extra bed:</td><td><ul><li>Deluxe Villa</li></ul></td></tr>
</table>
"""

SAMPLE_CSV = (
    "id,name,description,atoll,stars,photos,amenities,website,portal_hotel_id,portal_slug\n"
    "anantara-dhigu,Anantara Dhigu,First row,South Male Atoll,4,http://p1.jpg|http://p2.jpg,Wi-Fi|Pool,https://a.example,9001,anantara-dhigu\n"
    ",Kuramathi,Second row,Rasdhoo,4,,,https://k.example,9002,kuramathi-maldives\n"
)
