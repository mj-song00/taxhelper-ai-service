package texhelper.chatservice.common.exception;

import lombok.Getter;
import org.springframework.http.HttpStatus;

@Getter
public class BaseException extends RuntimeException {

    private final ExceptionEnum exceptionEnum;

    public BaseException(ExceptionEnum exceptionEnum) {
        super(exceptionEnum.getMessage());
        this.exceptionEnum = exceptionEnum;
    }

    @Override
    public String getMessage() {
        return exceptionEnum.getMessage();
    }

    public HttpStatus getStatus() {
        return exceptionEnum.getStatus();
    }

}