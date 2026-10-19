var tool = "OptiMathSAT";
var os_div = $('#os-div');
var arch_div = $('#arch-div');
var vers_div = $('#vers-div');
var down_div = $('#down-div');
var os = $('#os');
var arch = $('#arch');
var vers = $('#vers');
var down = $('#down');

function init () {
	// create JQ menu
	var smenu = $(".selectmenu");
	smenu.selectmenu().selectmenu('widget');

	detectSystem();
}

function hide(elements) {
	setTimeout(function() {
		for (i in elements) {
			elements[i].hide("slide", {}, 100, undefined);
		}
	}, 100);
}
function show(elements) {
	setTimeout(function() {
		for (i in elements) {
			elements[i].show("slide", {}, 200, undefined);
		}
	}, 300);
}

os.on("change selectmenuselect", function(e) {
	hide([arch_div, vers_div, down_div]);
	showArch();
	e.preventDefault();
});
arch.on("change selectmenuselect", function(e) {
	hide([vers_div, down_div]);
	showVers();
	e.preventDefault();
});
vers.on("change selectmenuselect", function(e) {
	hide([down_div]);
	showDown();
	e.preventDefault();
});

function showArch() {
	if ($("#os option:selected").val() === "macos") {
		disableElements(arch, "32-bit");
		disableElements(vers, "1.2.0");
		disableElements(vers, "1.2.1");
		disableElements(vers, "1.2.2");
		arch.selectmenu("refresh");
		vers.selectmenu("refresh");
	} else {
		enableElements(arch, "32-bit");
		enableElements(vers, "1.2.0");
		enableElements(vers, "1.2.1");
		enableElements(vers, "1.2.2");
		arch.selectmenu("refresh");
		vers.selectmenu("refresh");
	}
	show([arch_div]);
};
function showVers() {
    if ($("#arch option:selected").val() === "32-bit") {
        disableElements(vers, "1.6.0");
        disableElements(vers, "1.6.1");
        disableElements(vers, "1.6.2");
        disableElements(vers, "1.6.3");
        disableElements(vers, "1.6.4");
        disableElements(vers, "1.7.0");
        disableElements(vers, "1.7.0.1");
        disableElements(vers, "1.7.1");
        disableElements(vers, "1.7.2");
        disableElements(vers, "1.7.3");
    	vers.val("1.5.1");
        vers.selectmenu("refresh");
    } else {
        enableElements(vers, "1.6.0");
        enableElements(vers, "1.6.1");
        enableElements(vers, "1.6.2");
        enableElements(vers, "1.6.3");
        enableElements(vers, "1.6.4");
        enableElements(vers, "1.7.0");
        enableElements(vers, "1.7.0.1");
        enableElements(vers, "1.7.1");
        enableElements(vers, "1.7.2");
        enableElements(vers, "1.7.3");
    	vers.val("1.7.3"); // change this
        vers.selectmenu("refresh");
    }
	show([vers_div]);
};

function showDown() {
	var os_val = $("#os option:selected").val();
	var arch_val = $("#arch option:selected").val();
	var vers_val = $("#vers option:selected").val();
	var url = "../../releases/"
				+ tool.toLowerCase() + "-" + vers_val + "/"
				+ tool.toLowerCase() + "-" + vers_val
				+ "-" + os_val + "-" + arch_val
				+ (os_val === 'windows' ? "-mingw.zip" : ".tar.gz");

	down.on();
	down.attr("href", url);
	down.text("Download");
	down.switchClass("down-btn-end", "down-btn-init");
	down.addClass("down-btn-gen:hover");
	show([down_div]);
	down.click(function(e) {
		down.switchClass("down-btn-init", "down-btn-clicked");
		down.removeClass("down-btn-gen:hover");
		down.text("Downloading...");
		down.removeAttr("href");
		down.off();

		setTimeout(function() {
			down.switchClass("down-btn-clicked", "down-btn-end");
			down.text("Downloaded.");
		}, 1500);

    // FIXME: doesn't work on Firefox
	//	var form = $('<form method="get" target="_blank" action="url">'); //.attr('action', url);
	//	form.submit();
	//	e.preventDefault();

		// FIXME: alternative solution
		var link = document.createElement("a");
		$(link).click(function(e) {
			e.preventDefault();
			window.location.href = url;
		});
		$(link).click();
	});

	show([down]);
};

function getURLParameter(name) {
  return decodeURIComponent((new RegExp('[?|&]' + name + '=' + '([^&;]+?)(&|#|;|$)').exec(location.search) || [null, ''])[1].replace(/\+/g, '%20')) || null;
}

function detectSystem() {
	var version = navigator.appVersion
	var platform = "windows";
	var cpu_class = "32-bit"
	var versions = [
		"1.2.0", "1.2.1", "1.2.2", "1.2.3", "1.3.0", "1.3.1",
		"1.3.2", "1.3.3", "1.3.4", "1.3.5", "1.3.6", "1.3.7",
		"1.3.8", "1.3.9", "1.3.10", "1.4.0", "1.4.1", "1.4.2",
		"1.4.3", "1.4.4", "1.4.5", "1.5.0", "1.5.1", "1.6.0",
        "1.6.1", "1.6.2", "1.6.3", "1.6.4", "1.7.0", "1.7.0.1",
        "1.7.1", "1.7.2", "1.7.3"
	];
	var no_mac_vers = [ "1.2.0", "1.2.1", "1.2.2" ];
    var last_32_ver = "1.5.1";
	var cur_vers = "1.7.3";
	var sel_vers = cur_vers;

	// set version
	if (version.indexOf("x86_64") > -1 ||
			version.indexOf("WOW64") > -1 ||
			version.indexOf("Win64") > -1) {
		cpu_class = "64-bit";
	}

	// set platform
	if (version.indexOf("Linux") > -1 ||
		version.indexOf("X11") > -1 ||
		version.indexOf("Unix") > -1) {
		platform = "linux";
	} else if (version.indexOf("Mac") > -1) {
		platform = "macos";
		cpu_class = "64-bit";
	}

	// override values with parameters
	var ret = null;
	ret = getURLParameter("os");
	if (ret != null && (ret == "linux" || ret == "macos" || ret == "windows")) {
		platform = ret;
	}
	ret = getURLParameter("arch");
	if (ret != null && (ret == "64-bit" || ret == "32-bit")) {
		cpu_class = ret;
		if (platform == "macos") {
			cpu_class = "64-bit";
		}
	}
	ret = getURLParameter("version");
	if (ret != null && ret in versions) {
		sel_vers = ret;
		if (platform == "macos" && ret in no_macos_vers) {
			sel_vers = cur_vers;
		}
	}

console.log(sel_vers);

	// set values
	os.val(platform);
	arch.val(cpu_class);
	vers.val(sel_vers);

	os.selectmenu("refresh");
	arch.selectmenu("refresh");

	os.trigger("change");
	setTimeout(function() {
		arch.trigger("change");
	}, 200);
	setTimeout(function() {
		vers.trigger("change");	// latest
	}, 500);
};

var disableElements = function($selectList,selectedValue) {
	$selectList.find('option').each(function() {
		if($(this).val() === selectedValue) {
			$(this).attr('disabled','disabled');
		}
	});
};

var enableElements = function($selectList,selectedValue) {
	$selectList.find('option').each(function() {
		if($(this).val() === selectedValue){
			$(this).removeAttr('disabled');
		}
	});
};
