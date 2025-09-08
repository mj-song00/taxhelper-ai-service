package texhelper.chatservice.common.response;

import lombok.Getter;

@Getter
public enum ApiResponseEnum {

    ;


    private final String message;

    ApiResponseEnum(String message) {
        this.message = message;
    }

    public String getMessage() {
        return message;
    }
}
