$('document').ready(function(){
  $('.message').hide();
  var $btn = $('input[type=submit]');
  $btn.click(function(){
    $('#five .container').fadeOut();
    $('.message').delay().fadeIn();
  })
});